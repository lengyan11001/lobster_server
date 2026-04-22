# 富友支付 RSA 密钥放置目录

> 实际 `.pem` 文件被 `.gitignore` 排除，**永远不要入库**。

## 需要放进来的三把密钥

| 文件名 | 来源 | 用途 |
|---|---|---|
| `merchant_private_key.pem` | 商户自生成 / 富友测试环境提供 | 解密富友返回的 message；自签名 |
| `merchant_public_key.pem` | 与上面一对 | 商户上传给富友（开通时用一次，运行时其实不读） |
| `fuiou_public_key.pem` | 富友对接群下发 | 加密商户发送的 message；验证富友异步通知签名 |

## 文档参考

- 加密算法：<http://47.96.154.194/fuiouWposApipay/jie-kou-gai-shu/qian-ming-suan-fa.html>
- 报文格式：<http://47.96.154.194/fuiouWposApipay/jie-kou-gai-shu/bao-wen-ge-shi.html>
- 流程图（PC 主扫）：<http://47.96.154.194/fuiouWposApipay/liu-cheng-tu/liu-cheng-tu-pc.html>

## .env 路径配置

```env
FUIOU_MERCHANT_PRIVATE_KEY_PATH=certs/fuiou/merchant_private_key.pem
FUIOU_FUIOU_PUBLIC_KEY_PATH=certs/fuiou/fuiou_public_key.pem
```

支持相对工程根（`lobster-server/` 目录）或绝对路径。

## 自测

```bash
.venv/bin/python -c "
from backend.app.services.fuiou_pay import fuiou_configured
print('configured:', fuiou_configured())
"
```

返回 `True` 则配置 OK；返回 `False` 看 `backend.log` 里 `[fuiou] load key ...` 的报错。
