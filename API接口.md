# 跟单系统数据接口 v1

基础路径 `/api/v1/`，与原有网页共用数据库、账号和订单权限。使用 HTTPS 和同源 Session Cookie；未开放跨域 CORS，也没有默认公开访问或永久 Token。可以供未来的新前端、内部工具使用。现有网页继续直接使用同一 Django 后端，无须改为浏览器直连数据库。

金额、成本、单价和汇率返回十进制**字符串**。客户端写入也推荐字符串，不能先转换为 JavaScript 浮点数再核算。数量和记录 ID 为整数；日期 `YYYY-MM-DD`，时间 ISO 8601。不同销售币种单独汇总。

## 登录和请求保护

1. `GET /api/v1/auth/session/`，保留响应 Cookie，取得 `csrf_token`。
2. `POST /api/v1/auth/login/`，JSON `{ "username": "你的账号", "password": "你的密码" }`，请求头 `X-CSRFToken` 为上一步 token。
3. 登录成功后使用**新返回的** `csrf_token`；Session 在登录时轮换。
4. 后续所有 POST 携带 Cookie 和 `X-CSRFToken`。`POST /auth/logout/` 退出。

业务写入还必须提供 `Idempotency-Key: <UUID>`；例如 `crypto.randomUUID()`。同一账号、相同编号和相同内容的重试返回第一次提交结果，响应头含 `Idempotency-Replayed: true`。编号用于不同内容或路径返回 409。失败校验不会占用编号；已提交的编号不自动过期。重试时仍检查订单当前归属。登录、退出和发货预览不需要此编号。

网页登录和 API 登录共用限流：每账号10分钟8次、每IP10分钟40次（计入成功尝试），超过返回429。正式部署用 Redis 共享计数，代理覆盖真实IP请求头；多层代理需另行配置可信来源。不要公开应用8000端口。

## 接口表

下面路径均省略 `/api/v1/`。列表统一返回 `count / page / page_size / pages / results`，默认每页20条，最多100条。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `auth/session/` | 登录状态、当前用户、CSRF token |
| POST | `auth/login/` | 登录 |
| POST | `auth/logout/` | 退出 |
| GET / POST | `orders/` | 查询或创建大订单 |
| GET | `orders/{id}/` | 订单及多币种结算汇总 |
| GET / POST | `orders/{id}/lines/` | 独立小项及补单 |
| GET / POST | `orders/{id}/transactions/` | 收付款列表或管理员录入流水 |
| POST | `transactions/{id}/allocations/` | 管理员分配流水至小项或指定批次 |
| POST | `orders/{id}/shipments/preview/` | 计算发货占款预览，不写入 |
| GET / POST | `orders/{id}/shipments/` | 查询或确认实际发货 |
| GET | `transactions/{id}/attachment/` | 授权后下载私有凭证 |
| GET | `orders/{id}/export/` | 导出当前对账 ZIP，带 SHA-256 响应头 |
| POST | `orders/{id}/lock/` | 管理员核验结清并锁定归档，JSON `{}` |
| GET | `archives/{id}/download/` | 授权后下载固化归档 |

订单列表支持 `q=关键词`、`status=active|locked`、`page=1`、`page_size=20`。搜索同样只查询本人有权访问的订单。

`GET /health/live/` 检查应用存活；`GET /health/ready/` 检查数据库、订单表及缓存，无需登录且不泄露连接详情。状态异常返回503。未知 API 路径和校验异常使用JSON错误，不跳转登录页。

## 写入示例

创建订单：普通业务员可以省略 owner，后端固定为本人；管理员必须选择有效负责人。

```json
{"number":"ORD-EXAMPLE-001","customer":"虚构客户","owner":1,"date":"2026-10-28","notes":"示例"}
```

新增小项（同型号补单使用独立 label，不能合并）：

```json
{
  "label":"001","date":"2026-10-28","product":"示例产品","model":"A01",
  "quantity":100,"price":"12.3450","currency":"USD","quote_fx":"7.00000000",
  "cost":"50.0000","supplier":"虚构工厂","freight_included":true,"freight":"0.00",
  "customer_terms":"发货前结清","supplier_terms":"发货前结清"
}
```

管理员录入流水，kind 为 receipt（客户收款）或 payment（供应商付款）：

```json
{
  "kind":"receipt","date":"2026-10-28","amount":"500.00","currency":"USD",
  "party":"虚构客户","platform":"银行","fx":"1.00000000","fx_source":"人工确认",
  "purpose":"预付款","reference":"EXAMPLE-BANK-001"
}
```

带凭证时以 `multipart/form-data` 上传同名字段及 `attachment` 文件；让浏览器自动设置 multipart boundary。仅接受签名匹配的 PDF、PNG、JPG，文件最大10MB，请求总大小15MB。返回下载地址，不返回服务器文件路径。流水号在订单与类型内不能重复。

分摊（line 为小项ID，shipment_item 可省略或填空，表示未发货资金池）：

```json
{"line":1,"amount":"500.00","rate":"1.00000000","reason":"按约定收取预付款"}
```

存在已发货未结清批次时，必须传入该批次的**发货明细ID** `shipment_item`，不是发货单ID。来源流水剩余额度、小项余额、供应商名称及同币种汇率全部在后端检查。

发货预览只需要数量：

```json
{"quantities":{"1":40,"2":20}}
```

发货确认需要完整物流资料，以及一个固定 UUID token。token 为发货批次防重编号；同一批次重试保持相同 token 和 Idempotency-Key。确认时重新计算，预览不是占款承诺。

```json
{
  "token":"4c57422e-92bd-4125-b55d-21f8ba4e122a","date":"2026-10-28",
  "address":"虚构收货地址","carrier_type":"own","carrier":"示例货代",
  "domestic_date":"2026-10-28","domestic_tracking":"EXAMPLE-DOMESTIC",
  "international_tracking":"EXAMPLE-INTERNATIONAL",
  "freight":"100.00","freight_currency":"CNY","notes":"示例",
  "quantities":{"1":40}
}
```

carrier_type 为 `own` 或 `customer`。币种可选 USD / CNY / EUR / HKD / GBP。confirm 后 snapshot 不被后续预付款重新覆盖，最后一批承接舍入尾差。

## 权限和错误

只有 is_superuser 管理员可录入流水、分摊或锁定。业务员只能查看和操作 owner 是自己的订单；搜索、列表、明细、下载和重复请求也遵守此规则。锁定订单不能继续业务写入。没有删除或直接改写历史财务记录的接口。

```json
{"error":{"code":"validation_error","message":"具体错误说明","fields":{"amount":["金额校验说明"]}}}
```

常用状态码：200读取或成功操作，201创建，400字段/业务校验，401未登录，403权限/CSRF，404不存在或不可访问，405方法不支持，409请求编号或记录冲突，413请求过大，429登录限流，500内部错误，503未就绪。字段唯一约束可能在模型校验时返回400；数据库竞争冲突返回409。

生产数据库使用 PostgreSQL 的事务和大订单行锁；SQLite 仅本地验证。当前没有数据库 RLS，隔离依赖统一后端权限校验。接口不提供退款、冲销或自动销毁。
