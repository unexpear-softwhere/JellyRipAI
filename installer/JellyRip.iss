; Inno Setup script for JellyRip
; Compile with: ISCC installer\JellyRip.iss

#define MyAppName "JellyRip AI"
#define MyAppVersion "1.0.25"
#define MyAppPublisher "unexpear"
#define MyAppURL "https://github.com/unexpear-softwhere/JellyRipAI"
#define MyAppExeName "JellyRipAI.exe"
#define MyAppBuildOutputDir "..\dist\ai"

[Setup]
AppId={{F76B42D9-4AD5-482F-BF8B-5F53D73F5D91}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\Programs\JellyRip AI
DisableProgramGroupPage=yes
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Installer
VersionInfoProductName={#MyAppName}
VersionInfoOriginalFileName=JellyRipAIInstaller.exe
UsePreviousAppDir=yes
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no
OutputDir={#MyAppBuildOutputDir}
OutputBaseFilename=JellyRipAIInstaller
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked


[Files]
; One-DIR bundle: package the whole PyInstaller output folder.
; ffmpeg/ffprobe + their notices live inside _internal\ now — no
; separately staged copies.
Source: "{#MyAppBuildOutputDir}\JellyRipAI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Pre-onedir installs staged FFmpeg next to the exe, and the app
; prefers an exe-dir copy over the bundled one — without this an
; upgraded install would keep using the stale staged FFmpeg forever.
Type: files; Name: "{app}\ffmpeg.exe"
Type: files; Name: "{app}\ffprobe.exe"
Type: files; Name: "{app}\ffplay.exe"
Type: files; Name: "{app}\FFmpeg-LICENSE.txt"
Type: files; Name: "{app}\FFmpeg-README.txt"

[Icons]
Name: "{autoprograms}\JellyRip AI"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\JellyRip AI"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch JellyRip AI"; Flags: nowait postinstall skipifsilent

[Code]
{ ---- Dependency detection ---- }

function FindOnPath(const ExeName: String): Boolean;
begin
  Result := FileExists(FileSearch(ExeName, GetEnv('PATH')));
end;

function MakeMKVFound(): Boolean;
var
  ExePath: String;
  UninstStr: String;
begin
  Result := False;
  { MakeMKV's installer writes DisplayIcon (not InstallLocation) pointing
    directly to MakeMKVcon.exe. Check WOW6432Node first (32-bit app on 64-bit OS). }
  if RegQueryStringValue(HKLM,
      'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\MakeMKV',
      'DisplayIcon', ExePath) then
    if FileExists(ExePath) then begin Result := True; Exit; end;
  if RegQueryStringValue(HKLM,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MakeMKV',
      'DisplayIcon', ExePath) then
    if FileExists(ExePath) then begin Result := True; Exit; end;
  { Fallback: derive install dir from UninstallString }
  if RegQueryStringValue(HKLM,
      'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\MakeMKV',
      'UninstallString', UninstStr) then
    if FileExists(ExtractFilePath(UninstStr) + 'makemkvcon.exe') then begin Result := True; Exit; end;
  if RegQueryStringValue(HKLM,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MakeMKV',
      'UninstallString', UninstStr) then
    if FileExists(ExtractFilePath(UninstStr) + 'makemkvcon.exe') then begin Result := True; Exit; end;
  { Common install paths and PATH }
  Result :=
    FileExists('C:\Program Files (x86)\MakeMKV\makemkvcon.exe') or
    FileExists('C:\Program Files\MakeMKV\makemkvcon.exe') or
    FindOnPath('makemkvcon.exe');
end;

function FFprobeFound(): Boolean;
var
  InstallDir: String;
begin
  Result := False;
  { One-DIR bundle ships ffprobe inside _internal\ }
  if FileExists(ExpandConstant('{app}\_internal\ffprobe.exe')) then begin Result := True; Exit; end;
  if FileExists(ExpandConstant('{app}\ffprobe.exe')) then begin Result := True; Exit; end;
  { Registry checks — Chocolatey / winget (Gyan.FFmpeg) uninstall entries }
  if RegQueryStringValue(HKLM,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\ffmpeg',
      'InstallLocation', InstallDir) then
    if FileExists(InstallDir + '\ffprobe.exe') or
       FileExists(InstallDir + '\bin\ffprobe.exe') then begin Result := True; Exit; end;
  if RegQueryStringValue(HKLM,
      'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\ffmpeg',
      'InstallLocation', InstallDir) then
    if FileExists(InstallDir + '\ffprobe.exe') or
       FileExists(InstallDir + '\bin\ffprobe.exe') then begin Result := True; Exit; end;
  if RegQueryStringValue(HKLM,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Gyan.FFmpeg',
      'InstallLocation', InstallDir) then
    if FileExists(InstallDir + '\ffprobe.exe') or
       FileExists(InstallDir + '\bin\ffprobe.exe') then begin Result := True; Exit; end;
  { Common install / extract paths }
  Result :=
    FileExists('C:\tools\ffmpeg\bin\ffprobe.exe') or
    FileExists('C:\Program Files\ffmpeg\bin\ffprobe.exe') or
    FileExists('C:\Program Files\ffmpeg\ffprobe.exe') or
    FileExists('C:\Program Files (x86)\ffmpeg\bin\ffprobe.exe') or
    FileExists('C:\Program Files (x86)\ffmpeg\ffprobe.exe') or
    FileExists('C:\ffmpeg\bin\ffprobe.exe') or
    FileExists('C:\ffmpeg\ffprobe.exe') or
    FindOnPath('ffprobe.exe');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Msg: String;
begin
  if CurStep = ssDone then
  begin
    Msg := '';
    if not MakeMKVFound() then
      Msg := Msg + '  - MakeMKV   https://www.makemkv.com/download/' + #13#10;
    if not FFprobeFound() then
      Msg := Msg + '  - FFmpeg/FFprobe   https://ffmpeg.org/download.html' + #13#10;
    if Msg <> '' then
      MsgBox(
        'JellyRip was installed successfully.' + #13#10 + #13#10 +
        'However, the following required tools were not found on this PC:' + #13#10 + #13#10 +
        Msg + #13#10 +
        'Please install them before running JellyRip.' + #13#10 +
        'Custom paths can be set in JellyRip AI  Settings > Paths.',
        mbInformation, MB_OK);
  end;
end;

{ ---- Uninstall cleanup ---- }

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  RoamingDir: String;
  LocalDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RoamingDir := ExpandConstant('{userappdata}\JellyRipAI');
    LocalDir := ExpandConstant('{localappdata}\JellyRipAI');

    if DirExists(RoamingDir) or DirExists(LocalDir) then
    begin
      if MsgBox('JellyRip has been removed.' + #13#10 + #13#10 +
        'Do you also want to remove your settings and data?' + #13#10 +
        '(config, logs, cache in AppData)' + #13#10 + #13#10 +
        'Choose No to keep them for a future reinstall.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        if DirExists(RoamingDir) then
          DelTree(RoamingDir, True, True, True);
        if DirExists(LocalDir) then
          DelTree(LocalDir, True, True, True);
      end;
    end;
  end;
end;
