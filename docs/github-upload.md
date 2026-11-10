# 发布到 GitHub 私有仓库

`OpenPlatform-0.2.1-source.zip` 包含编排包、SDK、依赖锁、示例、测试、安装与验收文档、CPU Actions 工作流。权重、上游资产、虚拟环境、容器镜像和原始评测产物单独存放。用户可按固定上游版本在自己的机器准备镜像与资产。 [v0.2.1 发布说明](releases/v0.2.1.md)

## 已有仓库

1. 将本次源码改动合入目标分支，在 GitHub Actions 查看 `CPU checks`。源码 ZIP 可作为 Release 附件。
2. 在仓库的 **Releases** 创建草稿，选择包含本次代码的提交并创建标签 `v0.2.1`，标题填 `OpenPlatform v0.2.1`。
3. 将发布说明填入描述，上传源码 ZIP 和同名 `.sha256` 文件，标记为预发布。核对草稿后发布。

[GitHub Release 官方步骤](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository)

## 首次提交到新仓库

在 GitHub 创建空的 **Private** 仓库。解压源码包，进入包含 `README.md` 和 `.github/` 的 `OpenPlatform` 目录，将下面的仓库地址替换为目标地址。

```bash
git init -b main
git add --all
git diff --cached --stat
git commit -m "OpenPlatform 0.2.1 preview"
git remote add origin 'https://github.com/OWNER/REPOSITORY.git'
git push -u origin main
```

推送后按已有仓库的 Release 步骤操作。 [GitHub 导入本地源码说明](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-add-a-local-repository-to-github)
