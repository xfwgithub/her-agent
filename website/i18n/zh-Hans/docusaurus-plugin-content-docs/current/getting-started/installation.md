---
sidebar_position: 2
title: "安装"
description: "在 Linux、macOS 或通过 Termux 在 Android 上安装 her Agent"
---

# 安装

使用一行安装命令，两分钟内即可启动并运行 her Agent。

## 快速安装

### 一行安装命令（Linux / macOS）

基于 git 的安装方式，跟踪 `main` 分支，可立即获取最新变更：

```bash
curl -fsSL https://her-agent.nousresearch.com/install.sh | bash
```

### Android / Termux

her 现在也提供 Termux 感知的安装路径：

```bash
curl -fsSL https://her-agent.nousresearch.com/install.sh | bash
```

安装程序会自动检测 Termux 并切换到经过测试的 Android 流程：

- 使用 Termux `pkg` 安装系统依赖（`git`、`python`、`nodejs`、`ripgrep`、`ffmpeg`、构建工具）
- 使用 `python -m venv` 创建虚拟环境
- 自动导出 `ANDROID_API_LEVEL` 以用于 Android wheel 构建
- 优先使用较宽泛的 `.[termux-all]` extra，若首次编译失败则回退到较小的 `.[termux]` extra（最终回退到基础安装）
- 默认跳过未经测试的浏览器 / WhatsApp 引导

如需完整的显式步骤，请参阅专门的 [Termux 指南](./termux.md)。

### 安装程序做了什么

安装程序自动处理一切——所有依赖（Python、Node.js、ripgrep、ffmpeg）、仓库克隆、虚拟环境、全局 `her` 命令配置以及 LLM 提供商配置。完成后即可开始聊天。

#### 安装目录结构

安装程序的存放位置取决于你是以普通用户还是 root 身份安装：

| 安装方式                                | 代码位置                       | `her` 二进制                          | 数据目录                              |
| --------------------------------------- | ------------------------------ | ---------------------------------------- | ------------------------------------- |
| pip install                             | Python site-packages           | `~/.local/bin/her`（console_scripts） | `~/.her/`                          |
| 用户级（git 安装程序）                  | `~/.her/her-agent/`      | `~/.local/bin/her`（符号链接）        | `~/.her/`                          |
| Root 模式（`sudo curl … \| sudo bash`） | `/usr/local/lib/her-agent/` | `/usr/local/bin/her`                  | `/root/.her/`（或 `$HER_HOME`） |

Root 模式的 **FHS 布局**（`/usr/local/lib/…`、`/usr/local/bin/her`）与其他系统级开发工具在 Linux 上的安装位置一致。适用于共享机器部署场景，一次系统安装可服务所有用户。每个用户的个人配置（认证、技能、会话）仍位于各自的 `~/.her/` 或显式指定的 `HER_HOME` 下。

### 安装后

重新加载 shell 并开始聊天：

```bash
source ~/.bashrc   # 或：source ~/.zshrc
her             # 开始聊天！
```

如需稍后重新配置单项设置，使用以下专用命令：

```bash
her model          # 选择 LLM 提供商和模型
her tools          # 配置启用的工具
her gateway setup  # 配置消息平台
her config set     # 设置单个配置项
her setup          # 或运行完整的设置向导一次性配置所有内容
```

:::tip 最快路径：Nous Portal
一个订阅涵盖 300+ 个模型以及 [Tool Gateway](/user-guide/features/tool-gateway)（网络搜索、图像生成、TTS、云端浏览器）。无需逐一管理各工具的密钥：

```bash
her setup --portal
```

该命令一次性完成登录、设置 Nous 为提供商并开启 Tool Gateway。
:::

---

## 前置条件

**pip install：** 除 Python 3.11+ 外无其他前置条件，其余均自动处理。

**Git 安装程序：** 唯一的前置条件是 **Git**。安装程序自动处理其余一切：

- **uv**（快速 Python 包管理器）
- **Python 3.11**（通过 uv，无需 sudo）
- **Node.js v22**（用于浏览器自动化和 WhatsApp 桥接）
- **ripgrep**（快速文件搜索）
- **ffmpeg**（TTS 的音频格式转换）

:::info
你**无需**手动安装 Python、Node.js、ripgrep 或 ffmpeg。安装程序会检测缺失的依赖并自动安装。只需确保 `git` 可用（`git --version`）。
:::

:::tip Nix 用户
如果你使用 Nix（在 NixOS、macOS 或 Linux 上），有专门的配置路径，包含 Nix flake、声明式 NixOS 模块和可选容器模式。请参阅 **[Nix & NixOS 配置](./nix-setup.md)** 指南。
:::

---

## 手动 / 开发者安装

如果你想克隆仓库并从源码安装——用于贡献代码、从特定分支运行或完全控制虚拟环境——请参阅贡献指南中的[开发环境配置](../developer-guide/contributing.md#development-setup)章节。

---

## 非 Sudo / 系统服务用户安装

支持以专用非特权用户身份运行 her（例如 `her` systemd 服务账户，或任何没有 `sudo` 权限的用户）。安装路径中真正需要 root 权限的只有 Playwright 的 `--with-deps` 步骤，该步骤通过 `apt` 安装 Chromium 所需的共享库（`libnss3`、`libxkbcommon` 等）。安装程序会检测 sudo 是否可用，并在不可用时优雅降级——它会将 Chromium 二进制安装到服务用户自己的 Playwright 缓存中，并打印管理员需要单独运行的确切命令。

**推荐的分步方式（Debian/Ubuntu）：**

1. **一次性操作，以具有 sudo 权限的管理员用户身份**，安装 Chromium 所需的系统库：

   ```bash
   sudo npx playwright install-deps chromium
   ```

   （可在任意位置运行——`npx` 会自动获取 Playwright。）

2. **以非特权服务用户身份**，运行常规安装程序。它会检测到缺少 sudo，跳过 `--with-deps`，并将 Chromium 安装到用户本地的 Playwright 缓存中：

   ```bash
   curl -fsSL https://her-agent.nousresearch.com/install.sh | bash
   ```

   如果想完全跳过 Playwright 步骤——例如在无头环境中运行且不需要浏览器自动化——传入 `--skip-browser`：

   ```bash
   curl -fsSL https://her-agent.nousresearch.com/install.sh | bash -s -- --skip-browser
   ```

3. **使 `her` 对服务用户的 shell 可用。** 安装程序将启动器写入 `~/.local/bin/her`。系统服务账户通常具有不包含 `~/.local/bin` 的最小 PATH。可以将其添加到用户环境，或将启动器符号链接到系统位置：

   ```bash
   # 方案 A — 添加到服务用户的 profile
   echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

   # 方案 B — 系统级符号链接（以管理员身份运行）
   sudo ln -s /home/her/.her/her-agent/venv/bin/her /usr/local/bin/her
   ```

4. **验证：** `her doctor` 现在应能正常运行。如果出现 `ModuleNotFoundError: No module named 'dotenv'`，说明你在用系统 Python 调用仓库源码中的 `her` 文件（`~/.her/her-agent/her`），而非 venv 启动器（`~/.her/her-agent/venv/bin/her`）——请修正步骤 3。

同样的方式适用于 Arch（安装程序使用 pacman，具有相同的 sudo 检测逻辑）、Fedora/RHEL 和 openSUSE——这些发行版完全不支持 `--with-deps`，因此管理员始终需要单独安装系统库。安装程序会打印相应的 `dnf`/`zypper` 命令。

---

## 故障排查

| 问题                        | 解决方案                                                                           |
| --------------------------- | ---------------------------------------------------------------------------------- |
| `her: command not found` | 重新加载 shell（`source ~/.bashrc`）或检查 PATH                                    |
| `API key not set`           | 运行 `her model` 配置提供商，或 `her config set OPENROUTER_API_KEY your_key` |
| 更新后配置丢失              | 运行 `her config check`，然后运行 `her config migrate`                       |

如需更多诊断信息，运行 `her doctor`——它会告诉你确切缺少什么以及如何修复。

## 安装方式自动检测

her 会自动检测安装方式（`pip`、git 安装程序、Homebrew 或 NixOS），`her update` 会打印对应路径的更新命令。无需设置任何环境变量——检测基于安装目录结构（Python site-packages、`~/.her/her-agent/`、Homebrew 前缀或 Nix store 路径）。`her doctor` 也会在其环境摘要中显示检测到的安装方式。
