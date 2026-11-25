; Installateur Windows de NetTrace Pro (Inno Setup 6)
; Compilation : build.ps1 (ou ISCC.exe installer\NetTracePro.iss)

#define AppName "NetTrace Pro"
#define AppVersion "1.0.3"
#define AppExe "NetTracePro.exe"

[Setup]
AppId={{8B6E2F7A-4C1D-4E59-9A0B-5F3C2D1E7A90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SCHMOCK
AppPublisherURL=https://schmock.fr
AppSupportURL=https://schmock.fr
AppCopyright=© 2026 SCHMOCK
DefaultDirName={autopf}\NetTrace Pro
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installation pour l'utilisateur courant par défaut (pas de droits admin requis)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=NetTracePro-Setup-{#AppVersion}
SetupIconFile=..\assets\NetTracePro.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer une icône sur le Bureau"; GroupDescription: "Raccourcis :"
Name: "autostart"; Description: "Lancer NetTrace Pro au démarrage de Windows"; GroupDescription: "Options :"; Flags: unchecked

[Files]
Source: "..\build\app\NetTracePro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Documentation"; Filename: "{app}\README.md"
Name: "{group}\Désinstaller {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "NetTracePro"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent
