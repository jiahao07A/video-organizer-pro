; Video Organizer Pro Windows x64 installer
; Build input: PyInstaller onedir output in {#DistDir}\VideoOrganizer
; This script intentionally does not create or bundle the application's data directory.

#define AppName "Video Organizer Pro"
#define AppPublisher "Video Organizer Pro"
#ifndef AppVersion
  #error 请使用 tools/build_installer.py，从唯一版本来源读取版本号
#endif
#ifndef AppNumericVersion
  #error 缺少 AppNumericVersion
#endif
#define AppExeName "VideoOrganizer.exe"
#define AppId "{{D4FD6D1D-6D2D-4B2C-8B6D-000000000000}"
#ifdef IsolatedSmoke
  #undef AppId
  #define AppId "{{51A7B84D-3D41-4B87-A630-000000000051}"
#endif
#ifndef DistDir
  #define DistDir "..\\.release-build\\dist"
#endif

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Video Organizer Pro
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=..\release
OutputBaseFilename=VideoOrganizer-v{#AppVersion}-setup-win-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
DisableDirPage=no
DisableProgramGroupPage=yes
ChangesAssociations=no
AppMutex=VideoOrganizerPro.Application
; Ask Windows/Inno Setup to send a normal close request to the old app.
; The user can still cancel if the app refuses to close.
CloseApplications=yes
CloseApplicationsFilter=VideoOrganizer.exe
RestartApplications=no
UsePreviousAppDir=yes
#ifdef IsolatedSmoke
CreateUninstallRegKey=no
Uninstallable=no
#endif
VersionInfoVersion={#AppNumericVersion}.0
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppNumericVersion}
VersionInfoDescription={#AppName} Windows x64 installer

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："; Flags: unchecked

[Files]
Source: "{#DistDir}\VideoOrganizer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\软件更新与恢复.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; 升级安装时清除旧包误带的单个 ICU DLL，否则旧文件仍会遮蔽系统 ICU。
Type: files; Name: "{app}\_internal\icuuc.dll"

[Icons]
#ifndef IsolatedSmoke
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
#endif

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  CreateMutex('VideoOrganizerPro.Installing');
  Result := True;
end;

procedure InitializeWizard();
begin
  if ExpandConstant('{param:UPDATE|0}') = '1' then
  begin
    WizardForm.DirEdit.Enabled := False;
    WizardForm.DirBrowseButton.Enabled := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not SaveStringToFile(ExpandConstant('{app}\.video-organizer-install.json'),
      '{"app_id":"VideoOrganizerPro","version":"{#AppVersion}"}', False) then
      RaiseException('无法保存安装标记，请重新安装。');
  end;
end;

[UninstallDelete]
; The data directory is deliberately preserved. Users can remove it manually after review.
Type: filesandordirs; Name: "{app}\_internal"
