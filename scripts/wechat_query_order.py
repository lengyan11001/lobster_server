"""服务器上对指定 out_trade_no 执行微信查单并入账（不校验 user，管理员用）。用法: python scripts/wechat_query_order.py <out_trade_no>"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.core.config import settings
from backend.app.db import SessionLocal
from backend.app.models import RechargeOrder
from backend.app.api.billing import _apply_wechat_paid_to_order, _wechat_pay_configured

_BASE = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/wechat_query_order.py <out_trade_no>")
        sys.exit(2)
    out_trade_no = sys.argv[1].strip()
    if not out_trade_no:
        print("out_trade_no 为空")
        sys.exit(2)

    if not _wechat_pay_configured():
        print("未配置微信支付")
        sys.exit(1)

    db = SessionLocal()
    try:
        order = db.query(RechargeOrder).filter(RechargeOrder.out_trade_no == out_trade_no).first()
        if not order:
            print("订单不存在")
            sys.exit(1)
        if order.status == "paid":
            print("已支付", "order_id=%s credits=%s" % (order.id, order.credits))
            return

        key_path = Path((getattr(settings, "wechat_pay_private_key_path", None) or "").strip())
        if not key_path.is_absolute():
            key_path = _BASE / key_path
        private_key = key_path.read_text(encoding="utf-8")
        apiv3_key = (getattr(settings, "wechat_pay_apiv3_key", None) or "").strip()[:32]
        public_key_path_raw = (getattr(settings, "wechat_pay_public_key_path", None) or "").strip()
        public_key_id = (getattr(settings, "wechat_pay_public_key_id", None) or "").strip()
        public_key_content = None
        if public_key_path_raw and public_key_id:
            pub_path = Path(public_key_path_raw)
            if not pub_path.is_absolute():
                pub_path = _BASE / pub_path
            try:
                public_key_content = pub_path.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        from wechatpayv3 import WeChatPay, WeChatPayType
        mchid = (getattr(settings, "wechat_mch_id", None) or "").strip()
        kwargs = dict(
            wechatpay_type=WeChatPayType.NATIVE,
            mchid=mchid,
            private_key=private_key,
            cert_serial_no=(getattr(settings, "wechat_pay_serial_no", None) or "").strip(),
            apiv3_key=apiv3_key,
            appid=(getattr(settings, "wechat_app_id", None) or "").strip(),
        )
        if public_key_content and public_key_id:
            kwargs["public_key"] = public_key_content
            kwargs["public_key_id"] = public_key_id
        wxpay = WeChatPay(**kwargs)
        try:
            result = wxpay.query_order(out_trade_no=out_trade_no, mchid=mchid)
        except AttributeError:
            result = wxpay.query(out_trade_no=out_trade_no, mchid=mchid)

        if isinstance(result, (list, tuple)) and len(result) >= 2:
            code, body = result[0], result[1]
            if code != 200:
                print("微信查单非200", code)
                sys.exit(1)
            resp = body if isinstance(body, dict) else (json.loads(body) if isinstance(body, str) else {})
        else:
            resp = result if isinstance(result, dict) else {}

        trade_state = (resp.get("trade_state") or "").strip()
        if trade_state != "SUCCESS":
            print("未支付", "trade_state=%s" % trade_state)
            sys.exit(1)
        amount_info = resp.get("amount")
        paid_fen = int(amount_info.get("total")) if isinstance(amount_info, dict) and amount_info.get("total") is not None else None
        if paid_fen is None:
            print("查单结果无金额")
            sys.exit(1)
        wechat_transaction_id = (resp.get("transaction_id") or "").strip() or None
        if not _apply_wechat_paid_to_order(order, paid_fen, wechat_transaction_id, db):
            print("金额校验未通过")
            sys.exit(1)
        db.refresh(order)
        print("已入账", "order_id=%s credits=%s transaction_id=%s" % (order.id, order.credits, wechat_transaction_id))
    finally:
        db.close()


if __name__ == "__main__":
    main()
