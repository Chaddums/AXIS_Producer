# AXIS Session Log
Started: 2026-03-21 16:15

---

## [16:20] Batch 1

## Ideas Generated
- Using Hunyen 3D (10 cents image converter) for 2D to 3D car model conversion
- Creating a workflow that converts complex 3D geometries into simplified "toy car" style models
- Using nano banana to improve Claude's art output by acting as an intermediary refinement step

## Open Questions
- How to create a workflow that simplifies complex 3D geometries into toy-like models?
- What are the specific advantages of Hunyen 3D over other modeling programs?

## Key Discussion
- Leo (former head of choir team) tested six 2D to 3D modeling programs and found Hunyen 3D to be the clear winner
- Hunyen 3D is open source and performs best at Supra scans according to Leo's expert analysis
- Current workflow involves Claude generating art, passing it to nano banana for refinement, then back to Claude for improvement
- Nano banana has become preferred over previous tools since its release

---

## [16:25] Batch 2

## Decisions Locked
- Top five tasks will go to one person, bottom five to the other

## Ideas Generated
- AI image iteration systems may have been made "stupider" to reduce server load by preserving more existing content
- Use agents working in parallel for implementation tasks
- Tell the system to handle merging of parallel work streams

## Open Questions
- How to reliably merge work when multiple people work on one project?
- Which specific systems need to be removed vs built?

## Action Items
- Walk through the dock and document ideas
- Create list of all systems that need to be removed
- Create list of systems that need to build
- Create list of tools needed inside "junk air dander" 
- Set up clean slate with new tools before implementation starts

## Key Discussion
- Midjourney's iteration capability has degraded compared to early days when 15-20 edits were possible
- Current AI image tools make minimal changes to avoid regenerating entire images
- Need to coordinate parallel implementation work to avoid conflicts

---

## [16:30] Batch 3

## Ideas Generated
- AI tools can successfully merge complex, out-of-sync code files that would be difficult for humans to handle manually
- AI can ask clarifying questions during merge conflicts to preserve specific parts from different versions
- AI can rewrite tools by analyzing existing work rather than trying to preserve manual integration
- Current gameplay design could benefit from adding elements to make the character feel more powerful as low-cost experiments
- Two potential reward systems: temporary consumables for boss runs vs permanent unlockables

## Open Questions
- How do we make the core gameplay loop fun?
- Should rewards be temporary (consumables) or permanent (unlockables)?
- If unlockables, what specifically gets unlocked and how does it work?
- What should the reward system look like for activities that prepare you for boss fights?

## Key Discussion
- AI tool (Colladin) successfully merged two out-of-sync development branches in 40 minutes on first try
- Past example at Bungie where entire audio team lost 2 days of work due to branch integration failure with no recovery option
- Same type of problem would now take 10-15 minutes to resolve with AI tools
- Design approach of starting with current state and iteratively adding/removing features based on feel
- Concept of separate activities that provide rewards specifically for boss encounters

---

## [16:36] Batch 4

## Ideas Generated
- Central power spire immediately starts mining without needing to place mining buildings
- Ascendants are god-like AIs that join battles with their own objectives and don't always follow player commands
- Ascendants appear on both player and enemy sides with independent fighting styles
- Some Ascendants will obey player commands while others won't, requiring playstyle adaptation
- Ascendants make battles more glamorous and build narrative while maintaining player objectives
- Between runs, players take territory and save suits/builds from farming runs for boss runs
- Territory conquest could be planet-based, galaxy-based, or smaller scale

## Open Questions
- What should the central power structure be called beyond the dev name "spire"?
- What exactly are the enemies attacking during gameplay?
- What do bigger/better enemy units and player units look like?
- How should the territory/planet conquest system work specifically?
- How do we balance saved suits to avoid them being overpowered?

## Blockers
- Team blocked on finding assets for units and enemies

## Key Discussion
- Players lose individual rounds but still gain resources and progression
- Ascendants don't appear until later in most runs, making them rare extensions
- Resources gathered during runs are spent on upgrades between runs
- Saving suits allows using farming run builds in boss encounters

---

## [16:46] Batch 5

## Decisions Locked
- Suits system: achievements unlock suits with guaranteed 2-3 slots that persist between runs
- Suits are consumable when used for boss runs but can be used unlimited times in farming runs
- Relics found in farming runs go to inventory, limited number can be brought into boss runs
- Vine logic circuit system is one possible defense implementation, not core to the game
- Different factions (Taren, Protoss, Zurg) should have very different mechanics
- No floors system - waves come from one side initially, then multiple sides, then all sides at final stage

## Ideas Generated
- Farming runs vs boss runs distinction similar to Balatro deck building
- Mirror of Kalandra style rare item that can duplicate suits
- Threshold-based relic rewards in farming runs that can be applied to any saved suit
- Visual-only relics as flex items that show skill/achievement
- Relics with negative tradeoffs that enable powerful synergy builds (like POE2 chill builds)
- Dynamic audio ducking system for high-damage hits with accompanying visual effects
- Non-attacking character that buffs vine units, powering up circuits exponentially

## Open Questions
- What specific variabilities can builds have?
- What are nodes and how do they work?
- Should in-run relics be different from inventory relics?
- How many relics can be brought into boss runs?
- Should visual relics have small benefits or remain purely cosmetic?

## Action Items
- Figure out what nodes are and how they function
- Design perk adjustment system for different character types
- Build audio system that supports dynamic ducking for powerful hits
- Create visual effects that sell the impact of high-damage attacks

## Key Discussion
- Balatro-inspired persistence system where players can save powerful builds as "suits"
- Farming runs allow experimentation and building, boss runs consume saved builds
- Visual customization items serve as skill flex when players choose them over power
- Complex synergy builds using self-debuffs to enable powerful effects
- Each faction should feel distinctly different in gameplay mechanics, not just aesthetics

---

## [16:51] Batch 6

## Decisions Locked
- Enemies move toward center to try to destroy the central mining rig
- Level one towers should be functional by default without requiring additional components
- Game will start with players using one tower class, then later unlock mixed-class gameplay

## Ideas Generated
- Toggle between enemies attacking units vs. not attacking units to test both versions
- Signal chains system where building specific turrets next to each other creates unique interactions
- Three tower classes with radically different mechanics that don't initially mix
- Progression system similar to Slade's Fire where you unlock cross-class mechanics after completing runs
- Ascendants as big bots that fight both for and against players in boss battles
- Hades-style temporary summon system where ascendants help briefly then leave
- Visual spectacle of two gods/ascendants fighting each other

## Open Questions
- What specific mechanics will the three different tower classes have?
- Should ascendants stick around permanently or be temporary summons?
- How will players build toward getting specific ascendants?

## Action Items
- Add end game section showing mixed-class tower mechanics

## Key Discussion
- Motion sensor requirement for tower functionality rejected in favor of additive enhancement system
- Later game towers may have more advanced requirements than level one towers
- Ascendant system could work where you know you'll get one but not which specific one

---

## [16:56] Batch 7

## Ideas Generated
- Three different mining rigs with distinct defensive approaches: built-in turrets, regenerating shields, and high base regen/enemy pushback
- Each mining rig has different difficulty curves - some newbie-friendly, others more challenging initially
- Entry cracks open based on map milestones, creating escalating attack vectors
- Score UI dynamically grows/zooms as player approaches new high score
- AI character (Access) provides commentary when approaching resource milestones
- Temporary rewards system (discounted tokens/sodas) that creates debt mechanics
- Extraction shooter mechanics with risk/reward for staying longer vs leaving early
- Multiple "prison pockets" inventory system instead of single pocket like Ark

## Open Questions
- What balances the harder mining rigs that don't provide initial protection?
- How should the penalty system work for dying vs extracting early?
- What should the specific penalties be for death during resource runs?

## Key Discussion
- Game design philosophy of Tarrin/Prodos/Zerg taking different approaches to every mechanic
- Importance of making losses feel celebratory through post-game achievement recognition
- Need for escalating risk systems to push players out of safe farming zones

---

## [17:01] Batch 8

## Ideas Generated
- Death penalty system: randomly lose one item from slots when dying in a run
- Grid-based inventory system: 5x4 grid (20 slots) where items below a certain line are lost on death
- Item management restricted to spire visits only - no mid-run inventory shuffling
- Simple drag-to-swap item interface instead of unsocket/resocket mechanics
- Cowboy version of Bit character with lasso ability to capture enemies as units
- Progressive enemy capture system: start with walkers, level up to spiders, eventually capture descendants of gods
- Reference to Diablo 2 warlock class with tiered demon summoning/stealing abilities
- Consume mechanic where eating captured demons gives unique buffs (400+ unique demon buffs in D2 example)
- Farming runs for specific demon combinations (armor + Hephaesto + Lister combo example)
- Head hunter belt mechanic from PoE2 - capturing core attack principles from specific enemy types

## Key Discussion
- Inventory management during runs is problematic and should be avoided
- Spire should be central hub for all equipment management
- Enemy capture mechanics should scale with player progression
- Unique buffs per enemy type creates deep combo discovery gameplay
- Players enjoy finding optimal farming routes for specific buff combinations

---

## [17:12] Batch 10

## Ideas Generated
- Suit system where you can attach specific relics to suits and save builds as named configurations
- Home base ship/space station where players can view and manage their suits visually (not just menus)
- Deploy dialogue system with hundreds of variations from Bit character, never showing duplicates until all seen
- Narrative concept: Bit as ancient being who knows COVID-like catastrophe is coming but no one believes him
- Character inspiration mixing dark humor star from Mario, Tylana Moss from Mazalon, and Rocky from Hail Mary
- Bit as ancient superior being who intentionally acts positive/cute while having dark worldview
- Ascendant enemies who underestimate Bit due to his appearance, creating ironic power dynamic
- Core gameplay loop: Chase loot, suits, visual spectacle, ascendance, score chase across waves
- Resource/material hunting system for suit building
- Spire defense concept with instant pressure and limited starting resources
- Different harvester types offering different strategic tradeoffs
- Extraction mechanics similar to Helldivers 2/Risk of Rain
- "Fall" mechanic where losing still feels like winning

## Open Questions
- Should enemies attack from different directions or always from all directions?
- How to prevent players from just rushing all resource nodes at start of run?
- What creates meaningful expansion risk without rebuilding lost structures?
- Should it just be time investment to spread out, or something more engaging?
- Is attacking just the spire the right target system?

## Key Discussion
- Reference to sci-fi book "A Fire Upon the Deep" - humans as lower race trying to warn about AI threat
- Tylana Moss character concept: ancient undead who think only about futility after seeing everything
- Resource system currently has mining/harvester toggle but may remove micromanagement
- Waves expand map size rather than requiring base rebuilding
- Core tension: wanting expansion risk without reconstruction punishment

---

## [17:17] Batch 11

## Ideas Generated
- Balance challenge: successful defense runs vs. damaged base runs create difficulty scaling problems
- Boss runs are unique missions where failure is expected, different from regular gameplay
- Resource collection could work like Starcraft mining - risky ventures outside your base
- Deterministic high stakes for collecting suits, with farming runs vs boss runs distinction
- Character profiles: Bit (ancient, tired, experienced, dark humor), Access (nepo baby who inherited power), Ascendantor AI (focused on brute force over intelligence)
- Resource collection as tree of options in rogue-like style rather than claiming nodes
- Path choices during waves: accept harder enemies for resource bonuses
- Chaos nodes concept: 20% more income but with network impact/tradeoffs when linked
- Resource nodes spawn nearby rather than requiring base expansion
- Certain builds require linking to specific node types (chaos nodes)

## Open Questions
- What resource are we collecting and why is it important to all entities/ascendants?
- How do we correlate the theme with the resource collection mechanic?
- Can we always offer choices between different types of resource nodes?
- Should players also select which wave type comes next?

## Key Discussion
- Tower defense games struggle with comeback mechanics when players fall behind early
- Failed runs where you know you'll lose but have to wait 10 minutes feel bad
- Need to avoid the "might as well start over" feeling when losing a few towers
- Three main character archetypes established for narrative

---

## [17:22] Batch 12

## Ideas Generated
- Resource discovery triggers enemy ascendant spawns who want the same material
- Players start with base abilities, discover element types (chaos, lightning, etc.) during gameplay
- Ascendants spawn on map and will take resources if player doesn't act
- AI-as-antagonist theme could get press attention and commentary on current AI concerns
- Magic system explained as advanced AI technology that humans don't understand
- Different ascendants represent AI factions with distinct identities and power systems
- Faction-based gameplay where each map has specific enemy faction with their own ascendants
- Ascendants have dialogue/personality when they appear (snarky, god-like)
- Story structure: player thinks bit is weak and access is powerful, but relationship flips in act two

## Open Questions
- Should the player character be human or AI?
- How does access fit into the broader faction system?
- Is access an ascendant or just a faction boss/manager?
- Should there be human characters in the game at all?

## Key Discussion
- Magic types tied to specific ascendants creates strategic decisions about resource gathering
- AI theme allows "magic" to be explained as incomprehensible advanced technology
- Narrative setup involves mining company/faction structure with access as boss character
- Player exists as nondescript entity behind fourth wall vs having defined character

---

## [17:27] Batch 13

## Ideas Generated
- Replace access with the player character becoming one of the ascendants themselves
- Access is the leader of player's faction, with other syndicates having their own leaders (e.g. Broheem)
- Bit character as ancient AI being who sees all faction activities as pointless
- Player objective should be building toward confrontation with faction boss
- Memories bleeding out over time to reveal AI history and true purpose
- Unique dialogue lines across multiple runs to build toward narrative revelation

## Open Questions
- How is access different from other ascendants?
- Why would access exist if player becomes an ascendant?
- How do you communicate the narrative direction to players?
- Why is the narrative revelation relevant to roguelike runs?

## Key Discussion
- Access feels like vestige from original game concept
- "Bit is none of this stuff matters" doesn't work well for player motivation
- Player needs clear objective rather than nihilistic sidekick mentality
- Focus should be on player's goals even when interacting with other factions
- Conversation about player confronting boss should be at forefront of progression
- Can use Bit flipping on axis instead of needing third player character

## Watch List
- Disagreement about whether access character should remain in current form
- Concern that narrative direction may not serve roguelike gameplay loop

---

## [17:33] Batch 14

## Ideas Generated
- Roguelike structure with AI characters having their own objectives, controlled by player through one "cute little dude" character
- Player character understands more than other AIs but player only learns this through repeated playthroughs
- Ascendants appear based on material types during runs and have unique dialog lines
- When killing Ascendants, player gets unique rewards (relics, items, or run-specific buffs)
- Option to mine material and escape from Ascendants rather than fighting them if character is weak
- Farming runs could contribute items to a persistent stash rather than run-specific builds

## Open Questions
- How does material relate to items, equipment, and loadouts?
- What should the ultimate goal/final boss be and how should it be communicated to players?
- How should narrative be integrated without overwhelming the core gameplay loop?
- Should the game have linear progression or narrative twists/morality flips?
- What specific mechanics should express narrative to players during gameplay or loading?

## Watch List
- Disagreement about narrative complexity vs. simple roguelike structure
- Concern about shoehorning story elements that don't fit the core game
- Tension between one person's attachment to specific narrative vision vs. gameplay-first approach
- Disagreement about whether players should know the ultimate objective from the start

## Key Discussion
- Debate over whether complex narrative (memory loss, morality switches, faction dynamics) fits with tower defense/level progression gameplay
- One person advocates for clear linear progression toward stated goal, other prefers emergent storytelling through gameplay
- Discussion about balancing narrative depth with roguelike accessibility and replayability

---

## [17:38] Batch 15

## Key Discussion
- Materials overlay onto existing suit builds rather than replacing them
- Player can complete runs without collecting magical materials and still keep suit build
- Materials apply degrees of power rather than binary on/off states
- Boss runs may offer different materials/buffs if player enters without pre-collected materials
- Narrative design targets ~5 lines every 10 waves, with potential repeats
- Complete runs reward additional 2-3 lines of character interaction
- Memory bleed involves Bit remembering their identity through gameplay
- Environmental text needs 15-20 string announcements per map for materials, achievements, and run variants
- Need explanatory text for run variance so players understand what different elements mean

## Open Questions
- What are the specific requirements to "get" a suit?
- How exactly does the modular build system work for mixing materials?
- What constitutes the different degrees of material power (like chaos damage levels)?

---

## [17:43] Batch 16

## Ideas Generated
- Bosses could give hints on how to beat them through specific dialogue, though not obviously
- White towers as basic placeable units that vary by chosen deck/miner setup
- Random distribution of 2-3 white towers at run start (like basic jokers in similar games)
- Tower upgrade system using materials to modify function
- Breadcrumb system using dialogue lines to guide player strategy

## Open Questions
- What would a hint system actually look like as implemented gameplay?
- Should runs be confined to single planets or allow planet switching?

## Key Discussion
- Randomized roguelike elements through varied tool availability during runs
- Comparison to existing successful mechanics (Balatro jokers, basic tower types)
- Current systems inventory: vine logic, mind-building, two planets, waves/surges, four factions, access commentary, perk tree, junkyard editor, foreign system, basic cinematics
- Run structure clarification: one planet per run, no mid-run planet changes
- Design philosophy tension between specific implementation details vs. high-level system concepts

## Watch List
- Disagreement over focus on implementation details vs. system-level discussion
- Communication friction around critique of specific ideas vs. overall direction

---

## [17:48] Batch 17

## Key Discussion
- Wave system will be fairly consistent but add uniqueness depending on area/planet
- Environmental effects will vary by planet location
- Surges defined as the release cadence/timing of enemies within waves, not additional enemy mechanics
- Discussion of whether enemies should come constantly with shifting load/location vs. more structured waves
- Experimentation with surge alerts in tower defense showed alerts became annoying, so switched to dynamic enemy spawning from different sides
- Adam and speaker are working through dock-related tasks using markdown documentation

## Open Questions
- Do we want enemies constantly coming in with shifting loads and spawn locations?
- What specific environmental effects will different planets have?
- What uniqueness will different areas add to the wave system?

## Action Items
- Figure out next steps after completing dock discussion
- Text plan to team member after discussion with Adam

---

## [17:53] Batch 18

## Key Discussion
- Discussion about carpet shampooer usage and water running out
- Comparison of rental vs. purchase costs for carpet cleaning equipment (rental $80, purchase $400, pays for itself after 5 rentals)
- Weight issues with water-filled carpet shampooers requiring assistance to carry
- Marathon's cryo-archiving live event this weekend (raid equivalent/end game challenge)
- Current success rate for Marathon extraction is 0.7%

---

## [17:58] Batch 19

## Decisions Locked
- Cryo Archives confirmed to only be accessible on weekends
- Proxy chat follows in-game sound attenuation rules rather than simple distance-based volume
- No mechanics exist for enemy runners to revive non-allied runners
- Cryo has no early exit option - only way out is killing the boss or timing out

## Ideas Generated
- Optimal Cryo strategy should be multiple three-man squads cooperating rather than fighting
- Players could wait by loot room and steal rewards after others kill the boss
- Proxy chat could work like walkie-talkies with everyone on same channel at same volume

## Open Questions
- Is the current Cryo difficulty level intentional or broken?
- Should proxy chat be purely distance-based instead of environmentally attenuated?

## Watch List
- Less than 1% extraction rate from Cryo (most teams saying closer to 1 in 100 odds)
- Players timing out after killing boss but before reaching extraction
- Weekend-only access alienating weekday players
- Proxy chat distance is half the lethal range of most weapons
- Most Cryo runs end with zero successful extractions
- Players spending 4-5 runs farming keys just to attempt Cryo once

## Key Discussion
- Cryo requires 6 access keys that are consumed on entry, max 3 keys obtainable per run elsewhere
- Four corner objectives must be completed before boss access, but items become unavailable if other players claim them
- 30-minute timer creates pressure where PvP fighting early eliminates chances for all involved
- Loot room opens to entire map when boss dies, not just the team that killed it
- Game design trains players for PvP aggression but Cryo requires cooperation
- Audio team did minimal third-person perspective testing for proxy chat attenuation

---

## [18:03] Batch 20

## Ideas Generated
- Tower defense game where you're attacked from all sides and must maintain walls to prevent enemy floods
- Walls have more HP and are easier to replace than towers, creating strategic resource allocation decisions
- Unpredictable surge mechanic: one direction gets 2-3x harder enemies temporarily without announcement
- Two potential game focuses: holding back enemy waves vs. making strategic decisions against specific enemies
- Map design as key factor in determining gameplay feel and strategy
- Having harvesters with default walls that enemies must break through first
- Beyond All Reason style production system with single buildings enhanced by modifier towers
- Tug-of-war gameplay with constant unit streaming rather than batch production
- Strategic counters like nukes vs. anti-missile defenses vs. shields
- Large-scale multiplayer battles (15v15 or 30v30) with massive unit counts

## Open Questions
- Is the main challenge holding back surges or making strategic decisions against specific enemies?
- What should the general map design look like to support the gameplay?
- How do different map layouts (open square vs. complex pathing) change the game feel?

## Action Items
- Go into the map editor and make 20 different versions of maps to test
- Start playing out different map versions to see what feels better
- Watch videos of Beyond All Reason for reference

## Key Discussion
- Wall-based defense creates permanent weakness when towers are lost during breaches
- Surge mechanics create dynamic pressure without being telegraphed to players
- Production-focused RTS differs from unit-micromanagement RTS in strategic emphasis
- Map design will significantly influence whether the game feels more like wave defense or strategic combat

---

## [18:09] Batch 21

## Ideas Generated
- Objective-based building: what you're hunting for in a map defines your build strategy (material runs vs late game survival)
- Different objectives require different spatial strategies (snake-like builds for materials vs central fortification for late game)
- Players should have to choose one direction per map run rather than trying to do everything
- Balatro-style synergy system where different "hands" (tower combinations) create distinct playstyles
- Tower defense equivalent of playing different poker hands to avoid "just build everything" problem

## Open Questions
- How do we create the tower defense equivalent of playing different hands in Balatro?
- Should base towers have very specific mechanical functions to drive specialization?
- What prevents players from always using the same optimal tower combinations?
- Can we let the spire die and continue playing, or is spire survival always required?

## Key Discussion
- Current tower defense games suffer from "build everything for big damage" problem
- Need mechanics that force players to specialize based on what they're working with each run
- Various tower types identified: single target, AoE splash, AoE explosion, area slow, targeted slow, targeted disable, tower buffs, enemy debuffs
- Game should feel different moment-to-moment depending on your chosen strategy
- Unlike other roguelikes, this game requires movement rather than staying in one spot

---

## [18:14] Batch 22

## Ideas Generated
- Tower customization system with modular slots to drastically change tower functionality
- White towers as base mechanics that get altered by what you put into them
- Synergy-based builds (like Gatling + chain stun for perpetual enemy slowdown)
- Physics-based movement mechanics for units (pushing/pulling enemies around)
- Melee towers that punch and push enemies back in radius
- Gravity towers that create directional push effects to group enemies
- Oil and fire combination mechanics for elemental interactions
- Enemy designer tool rather than building bespoke enemies individually

## Open Questions
- Should enemies attack all player structures or just the central tower?
- What should the actual spectacle/respect goals be?
- What is the middle ground for narrative between different team member ideas?
- What meta should be established for character builds and units?

## Action Items
- Split up and clean up old design elements (assignments to be determined)
- Create updated markdown document with producer notes
- Have narrative system analyze existing narrative thoughts and find middle grounds
- Schedule second design round tomorrow with updated materials

## Key Discussion
- Tower customization could inject more character like MOBA heroes rather than just "walking stuns"
- Central-tower-only damage creates cleaner win/loss conditions vs. distributed damage requiring constant healing builds
- Team recognizes they're still in verbal dialogue phase, not actual brainstorming/testing phase yet
- Current enemy behavior: projectile attacks when in range, always moving toward central building

---

## [18:19] Batch 23

## Decisions Locked
- Build all three mechanics simultaneously rather than focusing on one mining rig
- Work from home desks tonight while staying on Zoom call
- Use Godot 4.6.1 with C# for development
- Create multiple mechanics quickly then decide which fit together best

## Ideas Generated
- Fast onboarding approach: immediately drop players into clear goal with rewarding progression
- Build mechanics as fast as possible then mix and match combinations
- Use stubbed/diamond implementations to quickly test concepts
- Stay muted on Zoom while working but pipe in with updates

## Action Items
- Update HTML to organize ideas as a list
- Agree on tonight's development targets
- Stub in all four game pieces by end of night
- Adjust existing perk tree
- Add suit system
- Plug relic system into gameplay
- Adjust level layout

## Key Discussion
- Game should avoid "kitty steps intro" and get players immediately engaged with interesting mechanics
- Priority is building something testable within a couple days to validate if concept is worth finishing
- Many systems already exist (perk tree, relic system) and just need integration
- Once mechanics are playable, it becomes much easier to evaluate what's fun vs not fun

---

