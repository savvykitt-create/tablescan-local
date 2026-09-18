#define MyAppName "TableScan Local"
#define MyAppVersion "1.0.3"
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

[UninstallDelete]
Type: filesandordirs; Name: "{app}\tools\__pycache__"
Type: filesandordirs; Name: "{app}\_internal\tablescan_local\setup\__pycache__"
Type: dirifempty; Name: "{app}\tools"
Type: dirifempty; Name: "{app}\_internal\tablescan_local\setup"
Type: dirifempty; Name: "{app}\_internal\tablescan_local"
Type: dirifempty; Name: "{app}\_internal"
Type: dirifempty; Name: "{app}"

[Messages]
ConfirmUninstall=Remove TableScan Local completely, including Slow mode, settings, history and internal document copies? Original documents and exported files will be kept.

[Code]
procedure CleanupFailed(MessageText: String);
begin
  Log(MessageText);
  SuppressibleMsgBox(MessageText, mbError, MB_OK, IDOK);
  Abort;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
  Arguments: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    Arguments := '--uninstall-data';
    if UninstallSilent then Arguments := Arguments + ' --silent';
    if not Exec(ExpandConstant('{app}\{#MyAppExeName}'), Arguments,
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      CleanupFailed('Could not start data cleanup. Reinstall TableScan Local and retry uninstall.');
    if ResultCode <> 0 then
      CleanupFailed('Data cleanup failed. Close TableScan and any Slow installer, then retry uninstall.');
  end;
end;
