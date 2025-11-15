#!/usr/bin/env python3
import os
import time
import json
import sys
import argparse
from pathlib import Path
from typing import Optional
from datetime import datetime

import requests
from dotenv import load_dotenv

def load_env():
    load_dotenv()
    cfg = {
        "endpoint": os.getenv("DIFY_ENDPOINT"),
        "api_key": os.getenv("DIFY_API_KEY"),
        "user_id": os.getenv("DIFY_USER_ID", "slack-history-import"),
        "channel_id": os.getenv("CHANNEL_ID"),
        "oldest_ts": os.getenv("OLDEST_TS", "").strip(),
        "latest_ts": os.getenv("LATEST_TS", "").strip(),
        "oldest_date": os.getenv("OLDEST_DATE", "").strip(),
        "interval_min": float(os.getenv("REQUEST_INTERVAL_MIN", "1")),
        "limit": int(os.getenv("LIMIT", "5")),
        "max_retries": int(os.getenv("MAX_RETRIES", "3")),
        "retry_interval_sec": int(os.getenv("RETRY_INTERVAL_SEC", "5")),
        "state_file": os.getenv("STATE_FILE", "./cursor.state.json"),
    }
    missing = [k for k,v in cfg.items() if k in ("endpoint","api_key","channel_id") and not v]
    if missing:
        print(f"[ERROR] Missing required env: {missing}", file=sys.stderr)
        sys.exit(1)
    # oldest_ts と latest_ts は空文字列なら None に変換（文字列のまま保持）
    if not cfg["oldest_ts"]:
        cfg["oldest_ts"] = None
    if not cfg["latest_ts"]:
        cfg["latest_ts"] = None
    if not cfg["oldest_date"]:
        cfg["oldest_date"] = None
    return cfg

def load_state(path: str) -> dict:
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"cursor": None, "batch_no": 0, "finished": False}

def save_state(path: str, state: dict):
    Path(path).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def build_inputs(cfg, cursor: Optional[str]):
    inputs = {
        "channel": cfg["channel_id"],
        "limit": cfg["limit"],
    }
    if cursor:
        inputs["cursor"] = cursor
    if cfg["oldest_ts"] is not None:
        inputs["oldest_ts"] = cfg["oldest_ts"]
    if cfg["latest_ts"] is not None:
        inputs["latest_ts"] = cfg["latest_ts"]
    return inputs

def is_older_than_threshold(oldest_dt_str: Optional[str], threshold_date_str: Optional[str]) -> bool:
    """
    oldest_dt が threshold_date より古い（または等しい）場合に True を返す。
    oldest_dt_str: "2024-04-02 02:00:39" 形式
    threshold_date_str: "2024-1-1" 形式
    どちらかが None の場合は False を返す（チェックしない）
    """
    if not oldest_dt_str or not threshold_date_str:
        return False

    try:
        # oldest_dt をパース（"2024-04-02 02:00:39" 形式）
        oldest_dt = datetime.strptime(oldest_dt_str, "%Y-%m-%d %H:%M:%S")
        # threshold_date をパース（"2024-1-1" や "2024-01-01" 形式に対応）
        threshold_dt = datetime.strptime(threshold_date_str, "%Y-%m-%d")
    except ValueError:
        # パースエラーの場合は、別の形式を試す
        try:
            # "2024-1-1" のような形式に対応
            parts = threshold_date_str.split("-")
            if len(parts) == 3:
                threshold_dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]))
            else:
                return False
        except (ValueError, IndexError):
            return False

    # oldest_dt が threshold_dt より古い（または等しい）場合に True
    return oldest_dt <= threshold_dt

def call_dify(cfg, inputs):
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": inputs,
        "response_mode": "blocking",
        "user": cfg["user_id"],
    }
    resp = requests.post(cfg["endpoint"], headers=headers, json=payload, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Non-200 from Dify: {resp.status_code} {resp.text}")
    body = resp.json()
    # 期待フォーマット：
    # {
    #   "message_size": 5,
    #   "oldest_dt": "2025-09-24 02:54:14",
    #   "next_cursor": "bmV4dF90czoxNzU4NjgyMjYyMjQ2NzU5"
    # }
    outputs = (body.get("data") or {}).get("outputs") or body  # 双方に対応
    return {
        "message_size": outputs.get("message_size"),
        "oldest_dt": outputs.get("oldest_dt"),
        "next_cursor": outputs.get("next_cursor"),
    }

def main():
    parser = argparse.ArgumentParser(description="Dify Slack history loop script")
    parser.add_argument("--once", action="store_true", help="Run only once for testing (no loop)")
    args = parser.parse_args()

    cfg = load_env()
    state = load_state(cfg["state_file"])

    if state.get("finished"):
        print("[INFO] finished=true in state. Exit without running.")
        return

    if args.once:
        print("[INFO] Test mode: Running once without loop.")
    else:
        print("[INFO] Start loop. Press Ctrl+C to stop.")
    print(f"[INFO] channel={cfg['channel_id']} oldest_ts={cfg['oldest_ts']} latest_ts={cfg['latest_ts']} oldest_date={cfg['oldest_date']} interval_min={cfg['interval_min']}")

    try:
        while True:
            state["batch_no"] = int(state.get("batch_no", 0)) + 1
            cursor = state.get("cursor")
            inputs = build_inputs(cfg, cursor)

            print(f"\n=== Batch {state['batch_no']} ===")
            print(f"[POST] {cfg['endpoint']}")
            print(f"[INPUTS] {inputs}")

            # リトライロジック
            retry_count = 0
            res = None
            while retry_count <= cfg["max_retries"]:
                try:
                    res = call_dify(cfg, inputs)

                    # 結果が全てNoneの場合はDify内部エラーと判断
                    msg_size = res.get("message_size")
                    oldest_dt = res.get("oldest_dt")
                    next_cursor = res.get("next_cursor")

                    if msg_size is None and oldest_dt is None and next_cursor is None:
                        if retry_count < cfg["max_retries"]:
                            retry_count += 1
                            print(f"[WARN] All fields are None (possible Dify internal error). Retry {retry_count}/{cfg['max_retries']}...")
                            time.sleep(cfg["retry_interval_sec"])
                            continue
                        else:
                            print(f"[ERROR] Max retries ({cfg['max_retries']}) reached with None response.", file=sys.stderr)
                            if args.once:
                                print("[INFO] Test mode: Exiting after max retries.")
                                save_state(cfg["state_file"], state)
                                sys.exit(1)
                            # 通常モードでは次の間隔で再試行
                            break
                    else:
                        # 正常なレスポンスを受信
                        break

                except Exception as e:
                    print(f"[ERROR] {e}", file=sys.stderr)
                    if retry_count < cfg["max_retries"]:
                        retry_count += 1
                        print(f"[INFO] Retry {retry_count}/{cfg['max_retries']} after {cfg['retry_interval_sec']}s...")
                        time.sleep(cfg["retry_interval_sec"])
                        continue
                    else:
                        print(f"[ERROR] Max retries ({cfg['max_retries']}) reached.", file=sys.stderr)
                        if args.once:
                            print("[INFO] Test mode: Exiting after error.")
                            save_state(cfg["state_file"], state)
                            sys.exit(1)
                        # 通常モードでは次の間隔で再試行
                        sleep_s = max(1, int(cfg["interval_min"] * 60))
                        print(f"[INFO] Sleep {sleep_s}s then retry batch…")
                        time.sleep(sleep_s)
                        break

            # リトライ後もエラーの場合はスキップ
            if res is None or (msg_size is None and oldest_dt is None and next_cursor is None):
                state["batch_no"] -= 1  # batch_noを戻す
                continue

            print(f"[RESULT] message_size={msg_size} oldest_dt={oldest_dt} next_cursor={next_cursor}")

            # カーソル更新
            state["cursor"] = next_cursor

            # oldest_date による終了判定
            if is_older_than_threshold(oldest_dt, cfg["oldest_date"]):
                print(f"[INFO] oldest_dt ({oldest_dt}) reached or passed OLDEST_DATE ({cfg['oldest_date']}). Finished.")
                state["finished"] = True
                save_state(cfg["state_file"], state)
                break

            # 打ち止め判定：next_cursor が空(None/"")なら終了
            if not next_cursor:
                print("[INFO] next_cursor is empty. Finished.")
                state["finished"] = True
                save_state(cfg["state_file"], state)
                break

            save_state(cfg["state_file"], state)

            # --once オプションが指定されていれば1回で終了
            if args.once:
                print("[INFO] Test mode: Completed one batch. Exiting.")
                break

            sleep_s = max(1, int(cfg["interval_min"] * 60))
            print(f"[INFO] Sleep {sleep_s}s …")
            time.sleep(sleep_s)

    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user (Ctrl+C). Saving state and exit.")
        save_state(cfg["state_file"], state)

if __name__ == "__main__":
    main()
