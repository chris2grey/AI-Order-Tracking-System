# AI 跟单系统 · Sites 在线试用版
这是同一仓库内的 Sites 适配版本，使用独立试用数据，不导入本机订单和凭证。
启动、测试与发布遵循 Sites 提供的 Vinext 构建流程；GitHub Actions 执行 npm ci、数据库迁移生成、核算和浏览器测试。
账号密码采用 PBKDF2，SESSION_KEY 与 BOOTSTRAP_ACCOUNTS 只通过 Sites 的运行时环境变量配置。
BOOTSTRAP_ACCOUNTS 格式为账号列表，每项包含 username、password、role。仅在数据库为空时初始化，之后不重置已有密码。
D1 中的每个订单以带版本号的完整文档保存；版本比较、账号版本校验与幂等回执在同一事务中提交。写入竞争会重读后校验。
R2 保存受权限保护的凭证、归档和导出文件。所有金额按字符串保存，并使用整数定点数计算。
