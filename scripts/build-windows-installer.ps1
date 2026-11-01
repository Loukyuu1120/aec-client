param(
    [Parameter(Mandatory=$true)][string]$Downloads,
    [Parameter(Mandatory=$true)][string]$BuildRoot,
    [Parameter(Mandatory=$true)][string]$OutputRoot,
    [Parameter(Mandatory=$true)][string]$InnoCompiler,
    [switch]$StageOnly,
    [ValidateSet('Guided','Bundled')][string]$RuntimeMode='Guided'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $BuildRoot 'app'
New-Item -ItemType Directory -Path $stage,$OutputRoot -Force | Out-Null

function Copy-Directory([string]$Source, [string]$Destination, [string[]]$Excluded=@()) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $copyArgs = @($Source,$Destination,'/E','/NFL','/NDL','/NJH','/NJS','/NP','/XD','__pycache__','.cache','/XF','*.pyc','direct_url.json')
    if ($Excluded.Count) { $copyArgs += $Excluded }
    & robocopy @copyArgs | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Copy failed: $Source" }
}

$pythonDirectory = Join-Path $stage 'python'
Expand-Archive -LiteralPath (Join-Path $Downloads 'python-3.12.10-embed-amd64.zip') -DestinationPath $pythonDirectory -Force
@('python312.zip','.','Lib/site-packages','../runtime/cpu','..','import site') | Set-Content -Encoding ascii (Join-Path $pythonDirectory 'python312._pth')
$site = Join-Path $pythonDirectory 'Lib\site-packages'
New-Item -ItemType Directory -Path $site -Force | Out-Null
Get-ChildItem (Join-Path $projectRoot '.venv\Lib\site-packages') -Force | ForEach-Object {
    if ($_.Name -match '^(torch|torchaudio|torchgen|functorch)([.-]|$)' -or $_.Name -eq '__pycache__') { return }
    if ($_.PSIsContainer) { Copy-Directory $_.FullName (Join-Path $site $_.Name) }
    elseif ($_.Extension -ne '.pyc') { Copy-Item -LiteralPath $_.FullName -Destination $site -Force }
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
if ($RuntimeMode -eq 'Guided') {
    $tensorSite=Join-Path $stage 'runtime\cpu'
    New-Item -ItemType Directory -Path $tensorSite -Force | Out-Null
    $wheels=@(Get-ChildItem (Join-Path $Downloads 'cpu-wheels') -Filter '*cpu*win_amd64.whl')
} else {
    $tensorSite=$site
    $wheels=@(Get-ChildItem (Join-Path $Downloads 'wheels') -Filter '*cu128*win_amd64.whl')
}
if ($wheels.Count -ne 2) { throw 'Expected pinned torch and torchaudio wheels.' }
foreach ($wheel in $wheels) {
    [System.IO.Compression.ZipFile]::ExtractToDirectory($wheel.FullName,$tensorSite,$true)
}
foreach ($directory in @('web','vendor','resources','LICENSES')) {
    Copy-Directory (Join-Path $projectRoot $directory) (Join-Path $stage $directory)
}
Copy-Directory (Join-Path $projectRoot 'models') (Join-Path $stage 'models') @('model.ckpt')
Copy-Item (Join-Path $projectRoot 'LICENSE') (Join-Path $stage 'LICENSE') -Force
Invoke-WebRequest 'https://www.apache.org/licenses/LICENSE-2.0.txt' -OutFile (Join-Path $stage 'LICENSES\PS4-APACHE-2.0.txt')
Invoke-WebRequest 'https://vb-audio.com/Services/licensing.htm' -OutFile (Join-Path $stage 'LICENSES\VB-Audio-embedding-permission.html')
Copy-Item (Join-Path $projectRoot 'launch.py') (Join-Path $stage 'launch.py') -Force
Copy-Item (Join-Path $projectRoot 'packaging\windows\sitecustomize.py') (Join-Path $site 'sitecustomize.py') -Force
Copy-Item (Join-Path $projectRoot 'packaging\windows\runtime_probe.py') (Join-Path $stage 'runtime_probe.py') -Force
Copy-Item (Join-Path $projectRoot 'packaging\windows\install_model.py') (Join-Path $stage 'install_model.py') -Force
Copy-Directory (Join-Path $Downloads 'vbcable') (Join-Path $stage 'drivers\vbcable')
New-Item -ItemType Directory -Path (Join-Path $stage 'prerequisites') -Force | Out-Null
foreach ($filename in @('vc_redist.x64.exe','MicrosoftEdgeWebView2RuntimeInstallerX64.exe')) {
    $source = Join-Path $Downloads $filename
    if ((Get-AuthenticodeSignature -LiteralPath $source).Status -ne 'Valid') { throw "Invalid prerequisite signature: $filename" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $stage 'prerequisites') -Force
}
if ((Get-AuthenticodeSignature -LiteralPath (Join-Path $stage 'drivers\vbcable\VBCABLE_Setup_x64.exe')).Status -ne 'Valid') {
    throw 'VB-CABLE package signature is not valid.'
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $compiler /nologo /target:winexe /platform:x64 "/out:$stage\AECStudio.exe" "/win32icon:$stage\resources\app.ico" /reference:System.Windows.Forms.dll (Join-Path $projectRoot 'packaging\windows\RuntimeLauncher.cs')
if ($LASTEXITCODE -ne 0) { throw 'Application launcher compilation failed.' }
& $compiler /nologo /target:winexe /platform:x64 "/out:$stage\python\AEC Studio.exe" "/win32icon:$stage\resources\app.ico" (Join-Path $projectRoot 'packaging\windows\RuntimeHost.cs')
if ($LASTEXITCODE -ne 0) { throw 'Application runtime host compilation failed.' }
& $compiler /nologo /target:library /platform:x64 "/out:$stage\resources\AECStudioAudio.dll" (Join-Path $projectRoot 'packaging\windows\AudioUsage.cs')
if ($LASTEXITCODE -ne 0) { throw 'Audio usage monitor compilation failed.' }
& $compiler /nologo /target:exe /platform:x64 "/out:$stage\AECStudioDriver.exe" (Join-Path $projectRoot 'packaging\windows\DriverHelper.cs')
if ($LASTEXITCODE -ne 0) { throw 'Driver helper compilation failed.' }
@'
AEC Studio 0.11.0 个人试用版

包含 Python、CPU 推理环境、Microsoft 运行库及 VB-CABLE 官方已签名虚拟声卡驱动。
CUDA 改为应用内引导配置：先检测现有可用环境，可用就直接复用；缺少程序组件时才按用户选择添加，不重复安装完整 CUDA Toolkit 或显卡驱动。
新增 GPU 组件仅写入当前用户目录，需约 2.9 GB 下载；启用新组件需要重开应用，不需要因此重启电脑。

安装需要管理员权限，网吧受限电脑请由管理员操作。
虚拟麦克风安装后立即检测可用性，不固定要求重启；仅当 Windows 明确要求时提示重启。
已有可用 VB-CABLE 时直接复用；驱动存在但设备异常时提示检查，不重新安装或重置。
安装程序不主动更改 Windows 默认播放和录音设备。

首次安装需要联网，从 SpeakerBeam 模型发布者下载约 65 MB 文件并核对 SHA-256。
该模型的发布页未明确声明权重再分发许可，因此安装包不直接二次分发这份权重。

VB-CABLE 来源：https://vb-audio.com/Cable/
VB-CABLE 为 Donationware，欢迎向原作者捐赠。原始驱动包和许可文件保留在 drivers\vbcable。
作者的现行内嵌分发许可：https://vb-audio.com/Services/licensing.htm
PS4 模型来源 TaurenMountain/PS4，发布许可 Apache-2.0。
其他组件许可随各自包保存。此构建不提供额外的商业许可保证。

安装包不包含开发者或任何用户的录音、声纹、聊天信息和设备设置。
声纹数据保存在当前用户的 LocalAppData\AECStudio\data，升级及卸载应用不会主动删除该目录。
卸载应用不会移除可能被其他软件共同使用的 VB-CABLE 驱动。

首次使用：录制个人声纹，选择“只保留我的声音”及“保留本人优先”。
Discord 等通话软件的麦克风输入请选择 CABLE Output。
可以在“启动与后台”选择登录 Windows 时启动、静默进入托盘、自动开启已保存的处理配置，以及关闭窗口后继续后台处理。
后台保留托盘图标，可打开、开始或停止处理、退出；缺少声纹或已保存设备时不会擅自改用其他设备。
可在设置中开启“持续优化我的声纹”，仅在“只保留我的声音 / 保留本人优先”处理时学习。
每 30 秒最多进行一次额外身份检查；仅保存声纹向量，不保存通话录音，也不训练模型权重。
暂停学习立即使用原始注册声纹；重新开启可继续已有学习。停止处理后可清除学习并恢复原始声纹。
学习有严格的原始声纹漂移限制，并跳过短字、身份不确定、疑似混合人声；不保证每次更新都提升听感。
短音信息不足时优先保留，因此极短的其他人声也可能漏过。

声音过滤测试新增三阶段引导、分段倒计时、真实输入/输出电平及处理前后音量对比。
测试完成后可直接试听处理后的声音，也可对照原声；默认只在内存中临时保留，退出或下一次测试后清除。
打开“将本次测试音频保存为本机文件”后才会保存 WAV；试听跟随 Windows 默认播放设备，请先暂停通话。
结果中的音量比、衰减和 200 毫秒片段统计不是字词识别准确率，重叠说话效果仍需回听确认。

新版统一桌面、安装程序、窗口、托盘和工作台的声波图标。
安装包移除推理不需要的开发头文件与测试文件；已有文件通过 SHA-256 校验后跳过重复释放。
Microsoft 运行库仅在缺少时释放到临时目录并安装，已有组件不重复写入应用目录。
运行环境文件全部一致且此前已完成检查时，升级会复用检查结果；损坏文件会重新释放。

过滤测试前可调整提取模型、计算设备、回声消除、降噪、增益、他人声压低幅度及短字保护。
测试使用当前保存的参数，报告会保存参数快照；修改后仍保留上次试听并提示重新测试。
本页测试为 16 kHz 本人声纹过滤链路，和 48 kHz 清晰通话模式的效果及延迟不同。
“他人声压低幅度”和高级声纹门槛仅对“保留本人优先（双模型）”生效。

安装版音频宿主已更名为 AEC Studio.exe，程序描述和产品名称为 AEC Studio。
新增默认开启的“按需使用麦克风”：开启按需处理后等待其他软件使用 CABLE Output，再启动物理麦克风采集。
其他软件关闭虚拟输入后，约 3 秒宽限期结束会停止处理并释放麦克风；“停止处理”会取消待命。
若占用检测异常，会暂停采集并提示，可停止后关闭按需模式改为持续处理。
部分语音软件即使未讲话或已静音，仍会保持输入流，这时会继续处理，并显示检测到的使用程序。
实际采集、注册声纹和过滤测试期间，Windows 的麦克风使用提示仍正常显示，不会关闭系统隐私提示。
'@ | Set-Content -Encoding utf8 (Join-Path $stage '安装须知.txt')

if (Test-Path (Join-Path $stage 'data')) { throw 'Personal data must never be staged.' }
$privateFiles = @(Get-ChildItem $stage -Recurse -File | Where-Object { $_.Name -in @('reference.wav','embedding.npy','active.json','adaptation.json','app.log','studio-settings.json') })
if ($privateFiles.Count) { throw 'Unexpected personal-data files in installer payload.' }
& (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'prepare-installer-payload.py') --stage $stage --manifest (Join-Path $BuildRoot 'payload-files.iss')
if ($LASTEXITCODE -ne 0) { throw 'Payload preparation failed.' }
Get-ChildItem $stage -Recurse -File | ForEach-Object {
    [pscustomobject]@{path=[System.IO.Path]::GetRelativePath($stage,$_.FullName);bytes=$_.Length}
} | ConvertTo-Json -Depth 3 | Set-Content -Encoding utf8 (Join-Path $BuildRoot 'payload-manifest.json')
Write-Output "Staged runtime: $stage"
if (-not $StageOnly) {
    & $InnoCompiler "/DStageRoot=$stage" "/DOutputRoot=$OutputRoot" (Join-Path $projectRoot 'packaging\windows\AECStudio.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
    Get-FileHash (Join-Path $OutputRoot 'AECStudio-Setup-0.11.2-x64.exe') -Algorithm SHA256 | Format-List
}
