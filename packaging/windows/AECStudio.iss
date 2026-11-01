#ifndef StageRoot
  #error StageRoot is required
#endif
#ifndef OutputRoot
  #error OutputRoot is required
#endif

[Setup]
#ifdef BenchmarkOnly
AppId=AECStudioPayloadBenchmark
AppName=AEC Studio Payload Benchmark
PrivilegesRequired=lowest
Uninstallable=no
CreateUninstallRegKey=no
#else
AppId={{97679BC7-A8C2-4D7E-A715-E32A20B897F2}
AppName=AEC Studio
PrivilegesRequired=admin
#endif
AppVersion=0.11.4
AppPublisher=AEC Studio Personal Build
DefaultDirName={autopf}\AEC Studio
DefaultGroupName=AEC Studio
UninstallDisplayIcon={app}\AECStudio.exe
OutputDir={#OutputRoot}
#ifdef BenchmarkOnly
OutputBaseFilename={#BenchName}
#else
OutputBaseFilename=AECStudio-Setup-0.11.4-x64
#endif
SetupIconFile={#StageRoot}\resources\app.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
WizardStyle=modern
Compression=lzma2/fast
SolidCompression=yes
LZMADictionarySize=65536
LZMANumBlockThreads=4
DiskSpanning=no
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
LicenseFile={#StageRoot}\LICENSE
InfoBeforeFile={#StageRoot}\安装须知.txt

[Languages]
Name: "chinesesimp"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
#ifdef BaselinePayload
Source: "{#StageRoot}\*"; DestDir: "{app}"; Excludes: "model.ckpt,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
#define PayloadRuntimeId "baseline"
#else
#include AddBackslash(StageRoot) + "..\payload-files.iss"
Source: "{#StageRoot}\prerequisites\vc_redist.x64.exe"; DestDir: "{tmp}\AECStudioPrerequisites"; Flags: deleteafterinstall solidbreak; Check: NeedsVcRuntime
Source: "{#StageRoot}\prerequisites\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"; DestDir: "{tmp}\AECStudioPrerequisites"; Flags: deleteafterinstall solidbreak; Check: NeedsWebView
#endif

[Icons]
#ifndef BenchmarkOnly
Name: "{group}\AEC Studio"; Filename: "{app}\AECStudio.exe"; WorkingDir: "{app}"; AppUserModelID: "AECStudio.Desktop"
Name: "{autodesktop}\AEC Studio"; Filename: "{app}\AECStudio.exe"; WorkingDir: "{app}"; Tasks: desktopicon; AppUserModelID: "AECStudio.Desktop"
#endif

[Run]
#ifndef BenchmarkOnly
Filename: "{app}\AECStudio.exe"; Description: "启动 AEC Studio"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent runasoriginaluser; Check: CanLaunch
#endif

[UninstallDelete]
Type: files; Name: "{app}\.runtime-ready"

[Code]
var
  RequiredRestart: Boolean;
  ReadyToLaunch: Boolean;
  PayloadReused, PayloadWritten: TStringList;
  RuntimeChanged: Boolean;

function ExitRunningStudio: Boolean;
var
  Request: Variant;
  Attempt: Integer;
begin
  Result := True;
#ifndef BenchmarkOnly
  if not CheckForMutexes('Local\AECStudioPersonalVoice') then Exit;
  Result := False;
  if WizardSilent then Exit;
  if MsgBox('AEC Studio 正在运行。点击“确定”将自动退出工作台并继续安装；点击“取消”退出安装。',
    mbConfirmation, MB_OKCANCEL) <> IDOK then Exit;
  try
    Request := CreateOleObject('WinHttp.WinHttpRequest.5.1');
    Request.SetTimeouts(2000, 2000, 2000, 3000);
    Request.SetProxy(1);
    Request.Open('POST', 'http://127.0.0.1:8767/api/app/quit', False);
    Request.SetRequestHeader('Content-Type', 'application/json');
    Request.Send('{}');
    if Request.Status <> 200 then begin
      Log('AEC Studio did not accept the exit request.');
      Exit;
    end;
  except
    Log('Unable to request AEC Studio exit: ' + GetExceptionMessage);
    Exit;
  end;
  for Attempt := 1 to 100 do begin
    if not CheckForMutexes('Local\AECStudioPersonalVoice') then begin
      Result := True;
      Exit;
    end;
    Sleep(100);
  end;
#endif
end;

function InitializeSetup: Boolean;
begin
  PayloadReused := TStringList.Create;
  PayloadReused.Sorted := True;
  PayloadWritten := TStringList.Create;
  PayloadWritten.Sorted := True;
  Result := ExitRunningStudio;
  if not Result then Log('Installation cancelled: AEC Studio is still running or exit was declined.');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
#ifndef BenchmarkOnly
  if not ExitRunningStudio then
    Result := 'AEC Studio 尚未退出，无法继续安装。请从托盘退出工作台后重试。';
#endif
end;

function NeedsPayloadFile(const RelativePath, ExpectedHash: String): Boolean;
var
  Target: String;
begin
  if PayloadReused.IndexOf(RelativePath) >= 0 then begin Result := False; Exit; end;
  if PayloadWritten.IndexOf(RelativePath) >= 0 then begin Result := True; Exit; end;
  Target := ExpandConstant('{app}\') + RelativePath;
  Result := True;
  if FileExists(Target) then
    try
      Result := CompareText(GetSHA256OfFile(Target), ExpectedHash) <> 0;
    except
      Result := True;
    end;
  if Result then begin
    PayloadWritten.Add(RelativePath);
    if (Pos('python\', Lowercase(RelativePath)) = 1) or
       (Pos('runtime\', Lowercase(RelativePath)) = 1) or
       (Pos('models\', Lowercase(RelativePath)) = 1) then RuntimeChanged := True;
  end else PayloadReused.Add(RelativePath);
end;

function NeedsVcRuntime: Boolean;
var
  Installed, Minor: Cardinal;
begin
  Result := not (RegQueryDWordValue(HKLM64,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) and
    RegQueryDWordValue(HKLM64,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Minor', Minor) and
    (Installed = 1) and (Minor >= 38));
end;

function NeedsWebView: Boolean;
var
  Version: String;
  Key: String;
begin
  Key := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := not ((RegQueryStringValue(HKLM32, Key, 'pv', Version) and (Length(Version) > 1)) or
    (RegQueryStringValue(HKLM64, Key, 'pv', Version) and (Length(Version) > 1)) or
    (RegQueryStringValue(HKCU, Key, 'pv', Version) and (Length(Version) > 1)));
end;

function PinnedModelReady: Boolean;
var
  Target: String;
begin
  Target := ExpandConstant('{app}\models\speakerbeam\model.ckpt');
  Result := False;
  if FileExists(Target) then
    try
      Result := CompareText(GetSHA256OfFile(Target), '453780d8cca4e2f22e6db85a7314874cdbc0d0ddf4cea61155023eb1feaa7ab5') = 0;
    except
      Result := False;
    end;
end;

procedure RunRequired(const FileName, Parameters, LabelText: String);
var
  Code: Integer;
begin
  WizardForm.StatusLabel.Caption := LabelText;
  if not Exec(FileName, Parameters, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
    RaiseException(LabelText + '：无法启动安装组件。');
  if (Code = 3010) or (Code = 1641) then
    RequiredRestart := True
  else if Code <> 0 then
    RaiseException(LabelText + '失败，错误码 ' + IntToStr(Code) + '。请联系网吧管理员。');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Helper: String;
  RuntimeId: AnsiString;
begin
  if CurStep <> ssPostInstall then Exit;
#ifdef BenchmarkOnly
  Log('Payload files reused: ' + IntToStr(PayloadReused.Count) + '; written: ' + IntToStr(PayloadWritten.Count));
  Exit;
#endif
  ReadyToLaunch := False;
  if NeedsVcRuntime then
    RunRequired(ExpandConstant('{tmp}\AECStudioPrerequisites\vc_redist.x64.exe'),
      '/install /quiet /norestart', '正在准备 Microsoft 运行库');
  if NeedsWebView then
    RunRequired(ExpandConstant('{tmp}\AECStudioPrerequisites\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'),
      '/silent /install', '正在准备应用界面运行库');
  Helper := ExpandConstant('{app}\AECStudioDriver.exe');
  if not Exec(Helper, '--check', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
    RaiseException('无法检测虚拟麦克风驱动。');
  if Code = 2 then
    RunRequired(Helper, '--install "' + ExpandConstant('{app}\drivers\vbcable\vbMmeCable64_win10.inf') + '"',
      '未检测到虚拟声卡，正在安装并等待 Windows 识别')
  else if Code = 3 then
    RaiseException('已检测到 VB-CABLE 驱动，但输入或输出设备不可用。请检查设备是否被禁用；不会重新安装现有驱动。')
  else if Code <> 0 then
    RaiseException('虚拟声卡检测失败，未尝试覆盖现有驱动。');
  if not PinnedModelReady then
    RunRequired(ExpandConstant('{app}\python\python.exe'),
      '"' + ExpandConstant('{app}\install_model.py') + '"',
      '正在从模型发布者下载声音模型（约 65 MB，请保持联网）');
  if RuntimeChanged or not LoadStringFromFile(ExpandConstant('{app}\.runtime-ready'), RuntimeId) or
     (RuntimeId <> '{#PayloadRuntimeId}') then begin
    RunRequired(ExpandConstant('{app}\AECStudio.exe'),
      '--self-test --output "' + ExpandConstant('{commonappdata}\AECStudio\runtime-check.json') + '"',
      '正在检查音频设备和运行环境（首次或环境变化时）');
    SaveStringToFile(ExpandConstant('{app}\.runtime-ready'), '{#PayloadRuntimeId}', False);
  end;
  Log('Payload files reused: ' + IntToStr(PayloadReused.Count) + '; written: ' + IntToStr(PayloadWritten.Count));
  ReadyToLaunch := not RequiredRestart;
end;

function NeedRestart: Boolean;
begin
  Result := RequiredRestart;
end;

function CanLaunch: Boolean;
begin
  Result := ReadyToLaunch and not RequiredRestart;
end;

procedure RemoveOwnStartup(const RootKey: Integer; const SubKey: String);
var
  Command: String;
begin
  if RegQueryStringValue(RootKey, SubKey, 'AECStudio', Command) and
    (Pos('"' + ExpandConstant('{app}\AECStudio.exe') + '"', Command) = 1) then
    RegDeleteValue(RootKey, SubKey, 'AECStudio');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Names: TArrayOfString;
  Index: Integer;
begin
  if CurUninstallStep <> usUninstall then Exit;
  RemoveOwnStartup(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run');
  if RegGetSubkeyNames(HKU, '', Names) then
    for Index := 0 to GetArrayLength(Names) - 1 do
      if Pos('S-1-5-', Names[Index]) = 1 then
        RemoveOwnStartup(HKU, Names[Index] + '\Software\Microsoft\Windows\CurrentVersion\Run');
end;
