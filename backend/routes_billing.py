"""Billing routes — Stripe checkout, webhooks, portal, subscription status."""

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

import auth
import db
from config import Config

router = APIRouter(prefix="/billing", tags=["billing"])
_config: Config | None = None


def init(config: Config):
    global _config
    _config = config
    stripe.api_key = config.stripe_secret_key


# --- Stripe product/price IDs (set after creating products in Stripe dashboard) ---
# These should be env vars in production. Hardcoded here for clarity.
# Override via config if needed.

TEAM_PRICE_ID = "price_1TElI7DKfzAhq2Qe7mtL2hsj"   # AXIS Team $49.99/mo
PRO_PRICE_ID = "price_1TElHtDKfzAhq2QeGkIRaNx6"   # AXIS Pro $24.99/seat/mo


class CheckoutRequest(BaseModel):
    tier: str  # "team" or "pro"
    team_id: str
    promo_code: str | None = None
    seats: int = 1  # only relevant for Pro tier
    success_url: str = "http://localhost:8080/dashboard.html?billing=success"
    cancel_url: str = "http://localhost:8080/dashboard.html?billing=cancel"


class CheckoutResponse(BaseModel):
    checkout_url: str
    session_id: str


class RedeemCodeRequest(BaseModel):
    team_id: str
    promo_code: str
    tier: str = "team"


@router.post("/redeem-code")
async def redeem_code(req: RedeemCodeRequest, user: dict = Depends(auth.get_current_user)):
    """Redeem a 100%-off promo code without going through Stripe checkout."""
    if req.team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    try:
        # Look up promotion code in Stripe
        promos = stripe.PromotionCode.list(code=req.promo_code, active=True, limit=1)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stripe error: {e}")

    if not promos.data:
        raise HTTPException(status_code=400, detail="Invalid code")

    promo = promos.data[0]
    coupon = promo.coupon

    # Only allow this path for 100% off coupons
    if not (coupon.percent_off == 100):
        raise HTTPException(status_code=400, detail="This code requires checkout")

    # Check if subscription already exists for this team
    existing = db.get_subscription_by_team(req.team_id)
    if existing:
        return {"redeemed": True, "tier": existing["tier"], "status": existing["status"]}

    # Create a free subscription record directly (no Stripe subscription needed)
    try:
        db.create_subscription(
            team_id=req.team_id,
            stripe_customer_id=f"free_{user['sub']}",
            stripe_subscription_id=f"free_{req.team_id}_{req.promo_code}",
            tier=req.tier,
            status="active",
            current_period_end=None,
            seats=20 if req.tier == "team" else 1,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create subscription: {e}")

    return {"redeemed": True, "tier": req.tier, "status": "active"}


@router.get("/check-promo")
async def check_promo(code: str):
    """Check if a promo code is valid and return discount info. No auth required."""
    try:
        promos = stripe.PromotionCode.list(code=code, active=True, limit=1)
    except Exception:
        return {"valid": False}

    if not promos.data:
        return {"valid": False}

    coupon = promos.data[0].coupon
    return {
        "valid": True,
        "percent_off": coupon.percent_off or 0,
        "amount_off": coupon.amount_off or 0,
        "duration": coupon.duration,
    }


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(req: CheckoutRequest, user: dict = Depends(auth.get_current_user)):
    if req.team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    if req.tier not in ("team", "pro"):
        raise HTTPException(status_code=400, detail="Tier must be 'team' or 'pro'")

    # Get or create Stripe customer
    db_user = db.get_user_by_id(user["sub"])
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    customer_id = db_user.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            email=db_user["email"],
            name=db_user["name"],
            metadata={"user_id": user["sub"], "team_id": req.team_id},
        )
        customer_id = customer.id
        db.set_user_stripe_customer(user["sub"], customer_id)

    # Build line items
    price_id = TEAM_PRICE_ID if req.tier == "team" else PRO_PRICE_ID
    if not price_id:
        # Fallback: create ad-hoc price if product IDs not configured yet
        # This lets us test before setting up Stripe products
        price_data = {
            "currency": "usd",
            "recurring": {"interval": "month"},
            "unit_amount": 4999 if req.tier == "team" else 2499,
            "product_data": {
                "name": f"AXIS {req.tier.title()}",
            },
        }
        line_items = [{
            "price_data": price_data,
            "quantity": 1 if req.tier == "team" else req.seats,
        }]
    else:
        line_items = [{
            "price": price_id,
            "quantity": 1 if req.tier == "team" else req.seats,
        }]

    # Build checkout session params
    session_params = {
        "customer": customer_id,
        "mode": "subscription",
        "line_items": line_items,
        "success_url": req.success_url,
        "cancel_url": req.cancel_url,
        "subscription_data": {
            "trial_period_days": 14,
            "metadata": {"team_id": req.team_id, "tier": req.tier},
        },
        "metadata": {"team_id": req.team_id, "tier": req.tier, "user_id": user["sub"]},
    }

    # Promo code support — allow user-entered codes or general promo codes
    if req.promo_code:
        # Look up the promotion code in Stripe
        promos = stripe.PromotionCode.list(code=req.promo_code, active=True, limit=1)
        if promos.data:
            session_params["discounts"] = [{"promotion_code": promos.data[0].id}]
        else:
            raise HTTPException(status_code=400, detail="Invalid promo code")
    else:
        session_params["allow_promotion_codes"] = True

    session = stripe.checkout.Session.create(**session_params)
    return CheckoutResponse(checkout_url=session.url, session_id=session.id)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events. No auth — verified by signature."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig, _config.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(data)
    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(data)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(data)
    elif event_type == "invoice.payment_failed":
        _handle_payment_failed(data)
    elif event_type == "invoice.paid":
        _handle_payment_succeeded(data)

    return {"received": True}


def _handle_checkout_completed(session: dict):
    """New subscription created via checkout."""
    team_id = session.get("metadata", {}).get("team_id")
    tier = session.get("metadata", {}).get("tier", "team")
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")

    if not team_id or not subscription_id:
        return

    # Fetch subscription details from Stripe
    sub = stripe.Subscription.retrieve(subscription_id)

    db.create_subscription(
        team_id=team_id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        tier=tier,
        status=sub.status,
        current_period_end=sub.current_period_end,
        seats=sub.get("quantity", 1),
    )


def _handle_subscription_updated(sub: dict):
    """Subscription status changed (active, past_due, trialing, etc)."""
    db.update_subscription(
        stripe_subscription_id=sub["id"],
        status=sub["status"],
        current_period_end=sub.get("current_period_end"),
    )


def _handle_subscription_deleted(sub: dict):
    """Subscription canceled/expired."""
    db.update_subscription(
        stripe_subscription_id=sub["id"],
        status="canceled",
        current_period_end=sub.get("current_period_end"),
    )


def _handle_payment_failed(invoice: dict):
    """Payment failed — mark subscription as past_due."""
    subscription_id = invoice.get("subscription")
    if subscription_id:
        db.update_subscription(
            stripe_subscription_id=subscription_id,
            status="past_due",
        )


def _handle_payment_succeeded(invoice: dict):
    """Payment succeeded — ensure subscription is active."""
    subscription_id = invoice.get("subscription")
    if subscription_id:
        sub = stripe.Subscription.retrieve(subscription_id)
        db.update_subscription(
            stripe_subscription_id=subscription_id,
            status=sub.status,
            current_period_end=sub.current_period_end,
        )


@router.get("/status")
async def subscription_status(team_id: str, user: dict = Depends(auth.get_current_user)):
    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    sub = db.get_subscription_by_team(team_id)
    if not sub:
        return {"has_subscription": False, "status": "none", "tier": None, "trial": False}

    return {
        "has_subscription": True,
        "status": sub["status"],
        "tier": sub["tier"],
        "seats": sub.get("seats", 1),
        "current_period_end": sub.get("current_period_end"),
        "trial": sub["status"] == "trialing",
    }


@router.post("/portal")
async def billing_portal(team_id: str, user: dict = Depends(auth.get_current_user)):
    """Create a Stripe Customer Portal session for self-serve billing management."""
    if team_id not in user.get("teams", []):
        raise HTTPException(status_code=403, detail="Not a member of this team")

    db_user = db.get_user_by_id(user["sub"])
    customer_id = db_user.get("stripe_customer_id") if db_user else None

    if not customer_id:
        raise HTTPException(status_code=400, detail="No billing account found")

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url="http://localhost:8080/dashboard.html",
    )
    return {"portal_url": session.url}
