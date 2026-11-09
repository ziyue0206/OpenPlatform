# 上传到 GitHub 私有仓库

`OpenPlatform-0.1.0-preview-source.zip` 是 0.1.0 测试版的源码包，包含 Python 包、SDK、依赖锁、示例、测试、安装文档和 CPU Actions 工作流。按本次发布选择暂不添加许可证。权重、上游资产、虚拟环境和验收原始文件单独存放。

## 首次提交

在 GitHub 创建一个空的 **Private** 仓库。解压源码包并进入包含 `README.md` 和 `.github/` 的 `OpenPlatform` 目录。将下面的仓库地址替换为刚创建的仓库。已配置 Git 身份和 GitHub 登录的终端可直接执行。[GitHub 导入本地源码说明](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)

```bash
git init -b main
git add --all
git diff --cached --stat
git commit -m "Initial OpenPlatform 0.1.0 test release"
git remote add origin 'https://github.com/OWNER/REPOSITORY.git'
git push -u origin main
```

推送后在 Actions 页查看 `CPU checks`。工作流安装锁定的 Python 依赖，运行 Ruff、CLI 和双环境 SDK 测试。RoboDojo 仿真验收在单独的 Linux RTX 环境执行。[本机 CPU 快速开始](../README.md#cpu-开发-fixture) · [RoboDojo 安装与支持范围](integrations/robodojo-v08/README.md)
