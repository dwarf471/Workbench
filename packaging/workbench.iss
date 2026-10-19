#define AppVersion "0.2.0"
[Setup]
AppId={{9DC65F0E-05F9-4C86-A3BD-B65EF784115F}
AppName=我的工作台
AppVersion={#AppVersion}
AppPublisher=PersonalWorkbench
DefaultDirName={localappdata}\Programs\PersonalWorkbench
DefaultGroupName=我的工作台
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=PersonalWorkbench-{#AppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\PersonalWorkbench.exe
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\dist\PersonalWorkbench\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\.build-tools\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\我的工作台"; Filename: "{app}\PersonalWorkbench.exe"
Name: "{autodesktop}\我的工作台"; Filename: "{app}\PersonalWorkbench.exe"

[Run]
Filename: "{app}\PersonalWorkbench.exe"; Description: "打开我的工作台"; Flags: nowait postinstall skipifsilent

[Code]
function HasWebView2: Boolean;
var Version: String;
begin
  Result := (RegQueryStringValue(HKLM32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'))
    or (RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var ResultCode: Integer;
begin
  Result := '';
  if not HasWebView2 then begin
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Result := '无法安装 WebView2，请检查网络连接后重试。'
    else if not HasWebView2 then
      Result := 'WebView2 尚未安装完成，请联网后重新运行安装程序。';
  end;
end;
