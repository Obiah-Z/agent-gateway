# AI Agent Gateway 容量基线报告

## 基本信息
- 生成时间：2026-06-28T17:02:55+08:00
- 机器：Linux-6.8.0-124-generic-x86_64-with-glibc2.35
- Git commit：09ab5c9
- Python 版本：3.12.12
- 原始报告数量：3
- 纳入基线场景数：3

## 场景基线

| 场景 | 请求数 | 并发 | 成功/失败 | 错误率 | 吞吐 req/s | E2E P95 ms | Agent P95 ms | Delivery P95 ms | 最大投递积压 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| delivery-local | 20 | 1 | 20/0 | 0.0 | 1436.25 | 0.696 | 0.0 | 0.696 | 20 |
| delivery-rabbitmq | 20 | 4 | 20/0 | 0.0 | 1923.049 | 0.52 | 0.0 | 0.52 | 20 |
| mock-local | 20 | 4 | 20/0 | 0.0 | 30790.974 | 0.204 | 0.176 | 0.03 | 0 |

## 瓶颈判断

### delivery-local

- 投递链路吞吐约 1436.25 req/s，最大投递积压 20，Delivery P95 约 0.696 ms。

### delivery-rabbitmq

- 投递链路吞吐约 1923.049 req/s，最大投递积压 20，Delivery P95 约 0.52 ms。

### mock-local

- 本地调度基线吞吐约 30790.974 req/s，E2E P95 约 0.204 ms。

## 使用边界

- `mock-local` 代表网关本地调度上限，不代表真实模型或飞书链路。
- `delivery-local` 代表本地文件投递 fallback/audit 路径，默认按单 worker 观察。
- `delivery-rabbitmq` 代表 RabbitMQ 分发和 DeliveryRuntime broker consume 路径，当前不包含真实 PostgreSQL 锁竞争。
- `model-real` 和 `feishu-send-real` 会调用真实外部服务，容量结论受 API 限流、网络和平台状态影响。
- 基线报告用于对比趋势，不应作为严格 SLA；正式 SLA 需要固定机器、固定依赖版本和多轮重复压测。

## 原始报告

- `workspace/reports/load-tests/baseline-delivery-local.json`：delivery-local
- `workspace/reports/load-tests/baseline-delivery-rabbitmq.json`：delivery-rabbitmq
- `workspace/reports/load-tests/baseline-mock-local.json`：mock-local
