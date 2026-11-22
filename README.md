# Silicone Materials Intelligence

完整部署包，用于每周自动收集、筛选、评分并发布硅橡胶技术情报。

## 部署
1. GitHub新建公开仓库 `silicone-intelligence`。
2. 解压本包，将所有文件上传到仓库根目录。
3. 打开 Settings > Pages，Source选择 GitHub Actions。
4. 打开 Actions > Weekly silicone intelligence > Run workflow。
5. 网站地址：`https://<username>.github.io/silicone-intelligence/`。

## 自动来源
MDPI Polymers RSS，以及Crossref中的Polymer、Rubber Chemistry and Technology、Journal of Applied Polymer Science、Polymer Chemistry、Silicon。

## 暂不自动抓取
Google Scholar、Google Patents、Espacenet和Dow默认禁用，避免不稳定抓取或违反网站限制。后续可接入邮件Alert或官方API。

## AI摘要（可选）
默认关闭。编辑 `config/settings.yml` 将 `ai.enabled` 改为true，并在Settings > Secrets and variables > Actions中新建 `AI_API_KEY`。不要把密钥写进文件。

## 调整关键词和权重
编辑 `config/keywords.yml`。每周一08:00（香港/上海时间）自动运行，也可随时手动运行。
