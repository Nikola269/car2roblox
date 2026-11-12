; Inno Setup script for the Car2Roblox installer wizard.
;
; Produces dist\Car2RobloxSetup.exe: a normal Windows "Setup Wizard" (Welcome / License-free /
; Select install location / Start Menu + optional Desktop icon / Install / Finish, with its own
; uninstaller registered in "Apps & features"). It packages the Car2Roblox.exe and
; car2roblox-cli.exe that PyInstaller already built into dist\.
;
; The Welcome/Finished pages and every other page's corner use the branded WizardImage.bmp /
; WizardSmallImage.bmp sitting next to this file (the same car artwork and colours as the app's
; own icon), instead of Inno Setup's plain default graphics. Re-run make_wizard_images.py after
; changing the app's look (assets/make_icon.py) to regenerate them.
;
; This file is normally compiled automatically by build_exe.bat (it shells out to ISCC.exe if
; Inno Setup is installed). To compile it by hand instead: install Inno Setup
; (https://jrsoftware.org/isdl.php, free), then either
;   - open this file in the Inno Setup IDE and press Compile (Ctrl+F9), or
;   - run:  iscc /DMyAppVersion=1.3.1 Car2Roblox.iss
; from this folder, after dist\Car2Roblox.exe already exists (run build_exe.bat's PyInstaller
; step first, or build_exe.bat as a whole).
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Car2Roblox"
#define MyAppPublisher "Car2Roblox"
#define MyAppExeName "Car2Roblox.exe"
#define MyAppCliExeName "car2roblox-cli.exe"

[Setup]
AppId={{2E7C9C9B-6B7B-4B7E-9B3B-9B6B7B6B7B6B}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=Car2RobloxSetup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
WizardImageFile=WizardImage.bmp
WizardSmallImageFile=WizardSmallImage.bmp
WizardImageStretch=no
DisableWelcomePage=no
ChangesAssociations=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\{#MyAppCliExeName}"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme skipifsourcedoesntexist

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} (command line)"; Filename: "{app}\{#MyAppCliExeName}"; Flags: runminimized; Check: FileExists(ExpandConstant('{app}\{#MyAppCliExeName}'))
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
