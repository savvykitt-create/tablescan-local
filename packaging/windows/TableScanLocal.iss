#define MyAppName "TableScan Local"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "TableScan Local contributors"
#define MyAppExeName "TableScanLocal.exe"

[Setup]
AppId={{A37CDA2B-01F5-4E9C-A997-802DB0581A78}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\TableScan Local
DefaultGroupName={#MyAppName}
OutputDir=..\..\release
OutputBaseFilename=TableScan-Local-{#MyAppVersion}-Setup-x64
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest

[Files]
Source: "..\..\dist\TableScanLocal\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

Source: "..\install_slow_mode.py"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "..\..\src\tablescan_local\slow_runtime.py"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "install-slow-mode.cmd"; DestDir: "{app}\tools"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

Name: "{group}\Install or repair slow mode"; Filename: "{app}\tools\install-slow-mode.cmd"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
