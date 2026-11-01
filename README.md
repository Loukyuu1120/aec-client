# AEC Studio

Windows 中文语音工作台：回声消除、降噪和个人声纹人声过滤，适用于 Discord、游戏语音及其他支持选择麦克风的应用。基于 [AEC Client](https://github.com/samudinzul/aec-client) 开发。

[下载最新安装包](https://github.com/Loukyuu1120/aec-client/releases/latest) · [查看发布记录](https://github.com/Loukyuu1120/aec-client/releases) · [反馈问题](https://github.com/Loukyuu1120/aec-client/issues)

## 功能

- **清晰通话**：48 kHz 回声消除与降噪。
- **只保留我的声音**：注册个人声纹，使用目标人声提取与身份校验抑制其他人声，提供短字保护。
- **试听与调参**：测试前调整模型、计算设备、降噪、增益及过滤参数；测试后对照播放原声和处理后的声音。
- **声纹优化**：可选的本地声纹向量更新，保留原始声纹，可暂停和清除学习结果。
- **托盘与自启**：支持开机启动、后台待命和自动开启已保存的处理配置。
- **按需使用麦克风**：在其他软件使用虚拟输入时开始采集，关闭输入后释放物理麦克风。

## 安装与首次使用

1. 在 [Releases](https://github.com/Loukyuu1120/aec-client/releases/latest) 下载 `AECStudio-Setup-版本号-x64.exe` 并运行，需要管理员权限。
2. 安装程序检测 Microsoft 运行库和 VB-CABLE，复用已有可用组件。首次安装需联网获取 SpeakerBeam 模型。
3. 打开工作台，选择实际麦克风、扬声器回声参考和 `CABLE Input` 输出。
4. 在“我的声纹”中由本人单独正常说话，录制并保存声纹；测试前调整参数，再对照试听。
5. 在 Discord 等语音应用中，把输入设备设为 **CABLE Output**，然后在工作台开启处理。

已有工作台运行时，安装提示中的“确定”会请求正常退出，等待关闭后继续；“取消”中止安装。静默安装检测到工作台运行会返回失败，不自动关闭它。

## 系统与 GPU

| 项目 | 说明 |
| --- | --- |
| 系统 | Windows 10 1809 或更新版本、Windows 11，x64 |
| CPU | 安装包包含 CPU 推理环境，可先使用 CPU |
| NVIDIA GPU | 在应用内检测并引导配置 CUDA 推理组件，复用兼容的已有环境 |
| 虚拟麦克风 | 使用官方已签名 VB-CABLE 驱动，先检测再决定是否安装 |
| 重启 | 不固定要求重启；驱动或运行库明确要求时才提示 |

GPU 配置不重复安装完整 CUDA Toolkit 或显卡驱动；新增 GPU 组件需要较大下载空间。网吧电脑的安装及设备权限需要管理员允许。

## 使用提示

- 人声提取不能保证完全消除其他人声。短音节信息不足时优先保留，因此极短的其他人声也可能漏过。
- 目标人声模式有缓冲延迟，适合先试听再判断是否满足当前通话场景；清晰通话模式与它使用不同的处理链。
- 语音软件即使未通话或已静音，也可能保持虚拟输入打开，此时按需模式会继续采集。实际采集时 Windows 正常显示麦克风隐私提示。
- 关闭窗口可能只会进入托盘；需要完全关闭时，在托盘菜单选择“退出”。

## 数据与隐私

音频处理和声纹校验在本机执行。安装版用户数据位于 `%LOCALAPPDATA%\AECStudio\data`，源码运行使用项目 `data` 目录。

测试音频默认仅在内存中临时保留，只有主动勾选保存时才写入文件。声纹学习更新向量，不保存通话录音，也不训练模型权重。升级与卸载保留个人数据，卸载不移除可能被其他软件共用的 VB-CABLE。

仓库和安装包均不包含开发者的个人声纹、测试录音或设备配置。

## 从源码运行

安装 Python 3.12 x64 后执行：

```bat
Setup.cmd
Start.cmd
```

设置脚本创建 `.venv`、安装依赖并下载目标提取模型，需要联网。NVIDIA 用户也可运行 `Setup-GPU.cmd`，或使用工作台内的 GPU 引导。

## 自动构建与 Release

仓库使用 [Windows Release 工作流](.github/workflows/release.yml)。推送 `v版本号` 标签后，GitHub Actions 在 Windows 上准备依赖、运行测试、构建安装包，并发布 Release，附带安装包及 SHA-256 校验文件。

版本号以 `packaging/windows/AECStudio.iss` 的 `AppVersion` 为准，标签必须匹配。例如当前安装程序版本为 `0.11.4`：

```sh
git tag v0.11.4
git push origin v0.11.4
```

也可在 Actions 页面手动运行工作流：默认只构建验证，勾选发布选项后创建 Release。已有 Release 不会被覆盖。普通代码推送不会自动生成新版本。

当前安装程序版本为 **0.11.4**，内嵌应用运行时版本为 **0.11.0**。本地构建参数及依赖说明见 [Windows 打包说明](packaging/windows/README.md)。

## 来源与许可

本项目保留原项目的 [LICENSE](LICENSE) 及第三方许可。PS4、SpeakerBeam、Python、PyTorch、Microsoft 运行库和 VB-CABLE 分别遵循其各自许可。

SpeakerBeam 权重通过安装流程从发布者下载并核对 SHA-256，安装包不直接分发该权重。VB-CABLE 原始驱动包及其许可文件随安装包保留。历史版本及上游构建说明见 [上游 README](docs/UPSTREAM_README.md)。
