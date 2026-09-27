#define MyAppName "YouCut"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif
#define MyAppPublisher "sHUBH"
#define MyAppExeName "YouCut.exe"
#define MyAppId "shubh.youcut.clipper.app"

[Setup]
; App Information
AppId={{E6394601-7F00-4C35-A2B8-9D1B5E8C7F00}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes

; Per-user install (no UAC / admin prompt required)
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=YouCut-Setup-v{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

; Icon configuration
SetupIconFile=clipgrab\assets\icons\youcut.ico
UninstallDisplayIcon={app}\icons\youcut.ico

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
Source: "dist\YouCut\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "clipgrab\assets\icons\youcut.ico"; DestDir: "{app}\icons"; Flags: ignoreversion
Source: "clipgrab\assets\icons\youcut.png"; DestDir: "{app}\icons"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icons\youcut.ico"; IconIndex: 0; AppUserModelID: "{#MyAppId}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\icons\youcut.ico"; IconIndex: 0; AppUserModelID: "{#MyAppId}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  DataDir: String;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userprofile}\.youcut');
    if not DirExists(DataDir) then
      DataDir := ExpandConstant('{userprofile}\.clipgrab');
    if DirExists(DataDir) then
    begin
      if MsgBox('Do you also want to delete your YouCut settings and download history?', mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(ExpandConstant('{userprofile}\.youcut'), True, True, True);
        DelTree(ExpandConstant('{userprofile}\.clipgrab'), True, True, True);
      end;
    end;
  end;
end;
