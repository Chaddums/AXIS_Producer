; AXIS Producer — Inno Setup Installer Script
; Requires: Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
; Build with: iscc installer.iss

[Setup]
AppName=AXIS Producer
AppVersion=0.2.0
AppPublisher=CouloirGG LLC
DefaultDirName={autopf}\AXIS Producer
DefaultGroupName=AXIS Producer
OutputDir=dist
OutputBaseFilename=AXIS_Producer_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\AXIS_Producer.exe
SetupIconFile=axis.ico
AppPublisherURL=https://couloirgg.com

[Dirs]
Name: "{app}\logs"

[Files]
; Copy entire PyInstaller output directory
Source: "dist\AXIS_Producer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AXIS Producer"; Filename: "{app}\AXIS_Producer.exe"
Name: "{autodesktop}\AXIS Producer"; Filename: "{app}\AXIS_Producer.exe"

[Run]
; Launch after install
Filename: "{app}\AXIS_Producer.exe"; Description: "Launch AXIS Producer"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up logs and settings on uninstall
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\tray_settings.json"
