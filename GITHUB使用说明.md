# GitHub 与数据保存

GitHub 仓库用于保存源码、数据库迁移、测试、页面和开发文档。建议使用私有仓库。

以下文件不会进入仓库：订单数据库、账号资料、收付款凭证、本地密钥、运行日志、Python 运行环境、换机完整包及私有数据包。`.gitignore` 已覆盖这些文件。

## 换电脑继续开发

1. 克隆仓库，在 Codex 打开项目目录。
2. 先阅读 AGENTS.md、开发交接.md 和 BUSINESS_RULES.md。
3. 准备 Python 3.12，创建虚拟环境并安装 requirements.lock。
4. 不迁移现有数据时，运行 migrate 并自行创建管理员；需要假数据时才运行 seed_demo 或 seed_scenarios。
5. 需要当前订单、账号和凭证时，通过受控本地方式转移之前的私有数据包，在首次启动前放到项目根目录并命名为 private-data.zip。执行 restore_data.py --if-needed 后再启动；已有数据库不会被覆盖。

Windows 示例：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
# 如果已有私有数据包，应先恢复，再执行数据库迁移。
.\.venv\Scripts\python.exe restore_data.py --if-needed
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py test tracker
```

若使用之前的 Windows 免安装完整包，可以直接运行，不需要从 GitHub 复制运行环境。完整包与私有数据包不要上传到仓库、Release 或 Issue 附件。

## 数据约束

Git 同步代码不会同步订单数据。两台电脑独立修改的数据库不能通过 Git 自动合并。更新代码前备份数据库和凭证，并按需要执行 migrate。

即使是私有仓库，也不要提交密钥、密码或真实客户数据。`.gitignore` 不会移除已经进入 Git 历史的文件；当前初始化提交已在暂存区再次检查。
