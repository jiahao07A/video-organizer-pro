; Video Organizer Pro Windows x64 installer
; Build input: PyInstaller onedir output in {#DistDir}\VideoOrganizer
; This script intentionally does not create or bundle the application's data directory.

#define AppName "Video Organizer Pro"
#define AppPublisher "Video Organizer Pro"
#define AppVersion "0.0.0"
#define AppExeName "VideoOrganizer.exe"
#define AppId "{{D4FD6D1D-6D2D-4B2C-8B6D-000000000000}"
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
DisableProgramGroupPage=yes
ChangesAssociations=no
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription={#AppName} Windows x64 installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#DistDir}\VideoOrganizer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The data directory is deliberately preserved. Users can remove it manually after review.
Type: filesandordirs; Name: "{app}\_internal"
