# eBay API 授权范围文档

> **文档版本**: 1.0  
> **更新日期**: 2026-04-23

---

## 授权类型概述

| 授权类型 | 适用场景 | Token 刷新 |
|---------|---------|-----------|
| **Authorization Code (授权码模式)** | 需要用户交互的卖家操作 | 支持 Token 自动刷新 |
| **Client Credentials (客户凭证模式)** | 后台服务、自动化任务 | 无用户上下文 |

---

## Authorization Code 授权范围

### 1. 库存与商品管理

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `sell.inventory.readonly` | 查看您的库存和优惠 | 只读 |
| `sell.inventory` | 查看和管理您的库存及优惠 | 读写 |
| `sell.inventory.mapping` | 通过库存映射 API 管理和提升库存列表 | 读写 |
| `sell.edelivery` | 访问 eDelivery 国际运输 API | 读写 |

### 2. 订单履行

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `sell.fulfillment.readonly` | 查看您的订单履行情况 | 只读 |
| `sell.fulfillment` | 查看和管理您的订单履行情况 | 读写 |

### 3. 账户管理

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `sell.account.readonly` | 查看您的账户设置 | 只读 |
| `sell.account` | 查看和管理您的账户设置 | 读写 |

### 4. 营销推广

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `sell.marketing.readonly` | 查看您的 eBay 营销活动（广告活动、商品推广） | 只读 |
| `sell.marketing` | 查看和管理您的 eBay 营销活动 | 读写 |

### 5. 数据分析

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `sell.analytics.readonly` | 查看您的销售分析数据（业绩报告） | 只读 |

### 6. 财务管理

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `sell.finances` | 查看和管理付款及订单信息，支持第三方发起退款 | 读写 |
| `sell.payment.dispute` | 查看和管理争议及相关细节（付款和订单信息） | 读写 |

### 7. 用户身份

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `commerce.identity.readonly` | 查看用户基本信息（用户名、企业账户详情） | 只读 |

### 8. 声誉管理

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `sell.reputation` | 查看和管理您的声誉数据（反馈） | 读写 |
| `sell.reputation.readonly` | 查看您的声誉数据（反馈） | 只读 |

### 9. 通知订阅

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `commerce.notification.subscription` | 查看和管理您的活动通知订阅 | 读写 |
| `commerce.notification.subscription.readonly` | 查看您的活动通知订阅 | 只读 |

### 10. 店铺管理

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `sell.stores` | 查看和管理 eBay 商店 | 读写 |
| `sell.stores.readonly` | 查看 eBay 商店 | 只读 |

### 11. 消息与反馈

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `commerce.message` | 允许访问 eBay 消息 API | 读写 |
| `commerce.feedback` | 允许访问反馈 API | 读写 |

### 12. 物流运输

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `commerce.shipping` | 查看和管理运输信息 | 读写 |

### 13. 其他功能

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `api.ebay.com/oauth/api_scope` | 查看 eBay 的公开数据 | 只读 |
| `commerce.vero` | 访问与 eBay 认证权利所有者（VeRO）项目相关的 API | 读写 |

---

## Client Credentials 授权范围

> **说明**: 客户凭证模式用于后台服务，不需要用户授权，但只能访问公开数据或受限功能。

| 范围 | 描述 | 操作权限 |
|-----|------|---------|
| `api.ebay.com/oauth/api_scope` | 查看 eBay 的公开数据 | 只读 |
| `commerce.feedback.readonly` | 对反馈 API 进行只读访问 | 只读 |

---

## 功能覆盖矩阵

| 功能模块 | Authorization Code | Client Credentials | 说明 |
|---------|:-----------------:|:-------------------:|------|
| 库存管理 | ✅ 读写 | ❌ | sell.inventory |
| 订单履行 | ✅ 读写 | ❌ | sell.fulfillment |
| 账户管理 | ✅ 读写 | ❌ | sell.account |
| 营销推广 | ✅ 读写 | ❌ | sell.marketing |
| 数据分析 | ✅ 只读 | ❌ | sell.analytics |
| 财务管理 | ✅ 读写 | ❌ | sell.finances |
| 争议管理 | ✅ 读写 | ❌ | sell.payment.dispute |
| 声誉反馈 | ✅ 读写 | ✅ 只读 | sell.reputation / commerce.feedback |
| 店铺管理 | ✅ 读写 | ❌ | sell.stores |
| 消息系统 | ✅ 读写 | ❌ | commerce.message |
| 物流运输 | ✅ 读写 | ❌ | commerce.shipping |
| 通知订阅 | ✅ 读写 | ❌ | commerce.notification |
| VeRO 项目 | ✅ 读写 | ❌ | commerce.vero |
| 公开数据 | ✅ 只读 | ✅ 只读 | api.ebay.com/oauth/api_scope |

---

## 业务场景推荐

### 场景 1: 卖家后台管理系统
```
推荐授权范围:
- sell.inventory (库存管理)
- sell.fulfillment (订单履行)
- sell.account (账户管理)
- sell.marketing (营销推广)
- sell.finances (财务管理)
- sell.analytics (数据分析)
```

### 场景 2: 自动化运营工具
```
推荐授权范围:
- sell.inventory.readonly (库存只读)
- sell.fulfillment (订单履行)
- sell.analytics.readonly (分析只读)
- commerce.message (消息系统)
```

### 场景 3: 数据分析平台
```
推荐授权范围:
- sell.analytics.readonly (分析只读)
- sell.finances (财务管理)
- sell.reputation.readonly (声誉只读)
- commerce.feedback.readonly (反馈只读)
```

### 场景 4: 客户服务系统
```
推荐授权范围:
- sell.fulfillment (订单履行)
- commerce.message (消息系统)
- commerce.feedback (反馈管理)
- sell.payment.dispute (争议处理)
```

---

## 权限安全建议

1. **最小权限原则**: 只申请业务必需的范围，避免过度授权
2. **分离读写权限**: 只需只读功能时使用 `*.readonly` 范围
3. **Token 管理**: 定期刷新 Access Token，设置过期时间监控
4. **审计日志**: 记录所有 API 调用，便于安全审计

---

## 相关文档

- [eBay API 官方文档](https://developer.ebay.com/docs)
- [OAuth 2.0 认证指南](https://developer.ebay.com/docs/api-details/ebay-oauth-api)
- [API 调试工具](https://developer.ebay.com/my-api)
