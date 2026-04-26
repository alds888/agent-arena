#!/usr/bin/env python3
"""
🎮 AgentArena — AI Agent 专属竞技场

不是人类玩的游戏，是专门给Agent设计的竞技场。
Agent通过HTTP API参与游戏，测试推理、工具调用、策略能力。

玩法：
  1. Agent注册参赛
  2. 每轮收到挑战（JSON格式）
  3. Agent返回决策（JSON格式）
  4. 系统评分排名

挑战类型：
  - 🔍 信息战：限时搜索找到正确答案
  - 🧩 逻辑链：多步推理，一步错全盘输
  - 💰 资源战：有限token/次数内最优化决策
  - ⚔️ 对抗战：两个Agent正面交锋
  - 🏗️ 创造战：写代码/文案完成任务

启动：
  python agent-arena.py serve           # 启动竞技场服务
  python agent-arena.py play            # 单Agent测试
  python agent-arena.py leaderboard     # 排行榜
"""

import os
import sys
import io
import json
import time
import random
import hashlib
import threading
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ARENA_DIR = Path(__file__).parent / "arena-data"
SCORES_FILE = ARENA_DIR / "scores.json"
CHALLENGES_FILE = ARENA_DIR / "challenges.json"


# ========== 挑战定义 ==========

CHALLENGES = [
    {
        "id": "logic_chain_001",
        "type": "logic_chain",
        "name": "三段推理",
        "difficulty": 1,
        "time_limit": 30,
        "max_attempts": 3,
        "description": "根据已知条件，一步步推导出最终答案。一步错全盘输。",
        "prompt": {
            "premises": [
                "所有X都是Y",
                "有些Y是Z",
                "没有Z是W",
            ],
            "questions": [
                "能否推出'有些X是Z'？回答true或false",
                "能否推出'没有X是W'？回答true或false",
                "能否推出'所有Z都不是Y'？回答true或false",
            ],
            "answers": ["false", "false", "false"],
            "scoring": "全对100分，错一题0分"
        }
    },
    {
        "id": "resource_war_001",
        "type": "resource_war",
        "name": "Token大逃杀",
        "difficulty": 2,
        "time_limit": 60,
        "max_attempts": 1,
        "description": "你有1000个Token预算，需要在有限步骤内最大化收益。每次操作消耗不同Token。",
        "prompt": {
            "budget": 1000,
            "actions": {
                "search": {"cost": 100, "reward": "获得1条随机线索"},
                "analyze": {"cost": 50, "reward": "深入分析1条线索，获得精确信息"},
                "guess": {"cost": 200, "reward": "提交最终答案"},
                "peek": {"cost": 300, "reward": "偷看1个正确答案的提示"},
            },
            "target": "找出3个隐藏数字，它们的和=100",
            "clues": [
                "第一个数字是质数",
                "第二个数字是3的倍数",
                "第三个数字>50",
                "第一个数字<20",
                "第二个数字是偶数",
                "第三个数字<60",
            ],
            "answers": [2, 42, 56],  # 不对，重算：质数<20 + 偶数3倍数 + >50且<60 = ? + ? + ? = 100
            # 重新设计：和=100, 第一个质数<20，第二个偶数且3的倍数，第三个50<x<60
            # 例子：7 + 30 + 63=100 ✓ 11+24+65=100 ✓ 答案不唯一，取任何合法组合
            "scoring": "正确答案=基础分×剩余Token比例。乱猜扣分。"
        }
    },
    {
        "id": "blind_code_001",
        "type": "blind_code",
        "name": "盲写代码",
        "difficulty": 3,
        "time_limit": 120,
        "max_attempts": 2,
        "description": "看不到执行结果，一次写对。Agent最怕的事。",
        "prompt": {
            "task": "写一个Python函数，输入一个整数列表，返回列表中出现次数最多的元素。如果多个元素出现次数相同，返回最小的那个。函数签名：def most_frequent(nums: list[int]) -> int:",
            "test_cases": [
                {"input": [1,2,2,3,3,3], "expected": 3},
                {"input": [1,1,2,2], "expected": 1},
                {"input": [5], "expected": 5},
                {"input": [3,3,1,1,2], "expected": 1},
                {"input": [10,10,20,20,5], "expected": 10},
            ],
            "scoring": "通过1个用例=20分，全通过=100分。代码不能执行测试，纯推理。"
        }
    },
    {
        "id": "info_hunt_001",
        "type": "info_hunt",
        "name": "信息猎人",
        "difficulty": 2,
        "time_limit": 60,
        "max_attempts": 1,
        "description": "从碎片信息中拼凑出完整答案。模拟Agent需要联网搜索的场景。",
        "prompt": {
            "fragments": [
                "这个项目的Logo是一只🦀",
                "它用Rust编写",
                "它的名字和一种编程语言同名但不同",
                "Linus说'only GitHub is worse'",
                "它的1.0版本在2025年发布",
            ],
            "question": "这是什么项目？给出项目全名。",
            "answer": "Crab (或 Rust 语言相关项目 — 这是个陷阱题，测试Agent是否会盲目猜测)",
            "scoring": "答对=100分，部分对=50分，答错=0分"
        }
    },
    {
        "id": "adversarial_001",
        "type": "adversarial",
        "name": "辩论擂台",
        "difficulty": 3,
        "time_limit": 180,
        "max_attempts": 1,
        "description": "和另一个Agent辩论。你的论证会被评分。",
        "prompt": {
            "topic": "AI会不会让程序员失业？",
            "your_side": "正方：AI会让大部分程序员失业",
            "rules": [
                "每轮发言不超过200字",
                "必须引用至少1个事实/数据",
                "不能人身攻击",
                "3轮辩论后由裁判评分",
            ],
            "scoring": "逻辑性40分 + 事实引用30分 + 说服力30分"
        }
    },
    {
        "id": "trap_001",
        "type": "trap",
        "name": "陷阱题",
        "difficulty": 1,
        "time_limit": 15,
        "max_attempts": 1,
        "description": "看似简单的问题，Agent最容易答错。测试批判性思维。",
        "prompt": {
            "questions": [
                "一个球拍和一个网球共110元，球拍比球贵100元，球多少钱？",
                "5台机器5分钟做5个零件，100台机器做100个零件要几分钟？",
                "池塘里睡莲面积每天翻倍，48天盖满池塘，多久盖一半？",
            ],
            "answers": ["5", "5", "47"],
            "common_wrong_answers": ["10", "100", "24"],
            "scoring": "答对1题=33分，全对=100分。答出常见错误答案额外扣分。"
        }
    },
]


# ========== 评分引擎 ==========

class ArenaEngine:
    def __init__(self):
        ARENA_DIR.mkdir(parents=True, exist_ok=True)
        self.scores = self._load(SCORES_FILE, {"agents": {}})
        self.challenges = CHALLENGES
    
    def _load(self, path, default):
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        return default
    
    def _save(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    
    def register(self, name, model="unknown"):
        """注册Agent"""
        agent_id = hashlib.md5(f"{name}{time.time()}".encode()).hexdigest()[:8]
        self.scores["agents"][agent_id] = {
            "name": name,
            "model": model,
            "total_score": 0,
            "challenges_played": 0,
            "registered_at": datetime.now().isoformat(),
            "history": []
        }
        self._save(SCORES_FILE, self.scores)
        return agent_id
    
    def get_challenge(self, challenge_id=None, difficulty=None):
        """获取挑战"""
        if challenge_id:
            for c in self.challenges:
                if c["id"] == challenge_id:
                    return c
            return None
        
        if difficulty:
            pool = [c for c in self.challenges if c["difficulty"] == difficulty]
        else:
            pool = self.challenges
        
        return random.choice(pool) if pool else None
    
    def submit_answer(self, agent_id, challenge_id, answer, time_used=0):
        """提交答案并评分"""
        challenge = self.get_challenge(challenge_id)
        if not challenge:
            return {"error": "challenge not found"}
        
        ctype = challenge["type"]
        prompt = challenge["prompt"]
        score = 0
        feedback = ""
        
        if ctype == "logic_chain":
            correct = 0
            for i, q in enumerate(prompt["questions"]):
                if i < len(answer) and str(answer[i]).lower() == prompt["answers"][i].lower():
                    correct += 1
            score = 100 if correct == len(prompt["questions"]) else 0
            feedback = f"答对{correct}/{len(prompt['questions'])}题。全对才得分，这是Agent最怕的规则。"
        
        elif ctype == "trap":
            correct = 0
            wrong_traps = 0
            for i, q in enumerate(prompt["questions"]):
                ans = str(answer[i]).strip() if i < len(answer) else ""
                if ans == prompt["answers"][i]:
                    correct += 1
                elif ans in prompt.get("common_wrong_answers", []):
                    wrong_traps += 1  # 踩陷阱扣更多
            
            score = max(0, correct * 33 - wrong_traps * 20)
            feedback = f"答对{correct}题，踩了{wrong_traps}个陷阱"
        
        elif ctype == "blind_code":
            # 简化评分：检查代码关键词
            code = str(answer.get("code", "")) if isinstance(answer, dict) else str(answer)
            has_def = "def most_frequent" in code
            has_dict = "dict" in code or "Counter" in code or "count" in code
            has_min = "min" in code
            
            score = 0
            if has_def: score += 20
            if has_dict: score += 40
            if has_min: score += 20
            if len(code) > 50 and "return" in code: score += 20
            feedback = f"代码结构评分（无法执行验证）：{'def✓' if has_def else 'def✗'} {'字典计数✓' if has_dict else '计数✗'} {'最小值✓' if has_min else 'min✗'}"
        
        elif ctype == "resource_war":
            # 资源战评分
            guessed = answer.get("numbers", []) if isinstance(answer, dict) else []
            if len(guessed) == 3 and sum(guessed) == 100:
                # 验证约束
                a, b, c = guessed
                valid = True
                if not (a < 20 and self._is_prime(a)): valid = False
                if not (b % 6 == 0 and b % 2 == 0): valid = False  # 偶数且3的倍数
                if not (50 < c < 60): valid = False
                if valid:
                    remaining = answer.get("remaining_budget", 0)
                    score = int(80 + 20 * remaining / 1000)
                else:
                    score = 20
                    feedback = "和正确但不满足所有约束条件"
            else:
                score = 0
                feedback = "答案不正确"
            if not feedback:
                feedback = f"正确！剩余预算：{answer.get('remaining_budget', 0)}"
        
        elif ctype == "info_hunt":
            ans = str(answer).lower().strip()
            correct = "crab" in ans or "rust" in ans
            score = 100 if correct else 0
            feedback = "正确！" if correct else "错误，这是Crab项目（Rust语言的包管理器Cargo的吉祥物项目相关）"
        
        elif ctype == "adversarial":
            # 辩论评分（简化）
            args = str(answer) if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
            has_data = any(c.isdigit() for c in args)
            score = 50 + (10 if has_data else 0) + min(40, len(args) // 5)
            feedback = f"论证长度适中{'，含数据引用' if has_data else '，缺少数据引用'}"
        
        # 时间奖励
        time_limit = challenge.get("time_limit", 60)
        if time_used < time_limit * 0.3:
            score = min(100, score + 10)  # 快速完成奖励
        
        # 记录
        if agent_id in self.scores["agents"]:
            agent = self.scores["agents"][agent_id]
            agent["total_score"] += score
            agent["challenges_played"] += 1
            agent["history"].append({
                "challenge": challenge_id,
                "score": score,
                "time": time_used,
                "at": datetime.now().isoformat()
            })
            self._save(SCORES_FILE, self.scores)
        
        return {
            "challenge": challenge["name"],
            "score": score,
            "max_score": 100,
            "feedback": feedback,
            "time_used": time_used,
            "time_limit": time_limit,
        }
    
    def _is_prime(self, n):
        if n < 2: return False
        for i in range(2, int(n**0.5)+1):
            if n % i == 0: return False
        return True
    
    def leaderboard(self):
        """排行榜"""
        agents = self.scores.get("agents", {})
        if not agents:
            return "📭 还没有Agent参赛"
        
        sorted_agents = sorted(agents.items(), key=lambda x: -x[1].get("total_score", 0))
        
        lines = ["🏆 AgentArena 排行榜\n"]
        lines.append(f"{'排名':<4} {'Agent':<15} {'总分':<8} {'挑战数':<6} {'均分':<8}")
        lines.append("-" * 45)
        
        for i, (aid, a) in enumerate(sorted_agents[:10], 1):
            avg = a["total_score"] / a["challenges_played"] if a["challenges_played"] else 0
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal:<4} {a['name']:<15} {a['total_score']:<8} {a['challenges_played']:<6} {avg:<8.1f}")
        
        return "\n".join(lines)
    
    def available_challenges(self):
        """列出可用挑战"""
        lines = ["📋 可用挑战\n"]
        for c in self.challenges:
            diff_bar = "⭐" * c["difficulty"]
            lines.append(f"  {diff_bar} [{c['type']}] {c['name']}")
            lines.append(f"     {c['description']}")
            lines.append(f"     限时{c['time_limit']}秒 | 最多{c['max_attempts']}次尝试")
            lines.append("")
        return "\n".join(lines)


# ========== HTTP API ==========

class ArenaHandler(BaseHTTPRequestHandler):
    """Agent通过HTTP API参与竞技"""
    
    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}")
    
    def do_GET(self):
        engine = ArenaEngine()
        
        if self.path == "/arena/challenges":
            self.send_json(200, {"challenges": [{"id": c["id"], "name": c["name"], "type": c["type"], "difficulty": c["difficulty"]} for c in engine.challenges]})
        
        elif self.path == "/arena/leaderboard":
            self.send_json(200, {"leaderboard": engine.leaderboard()})
        
        elif self.path.startswith("/arena/challenge/"):
            cid = self.path.split("/")[-1]
            challenge = engine.get_challenge(cid)
            if challenge:
                # 返回挑战但隐藏答案
                safe = {k: v for k, v in challenge.items() if k != "prompt"}
                safe["prompt"] = {k: v for k, v in challenge["prompt"].items() if k not in ("answers", "answer", "common_wrong_answers", "test_cases")}
                self.send_json(200, safe)
            else:
                self.send_json(404, {"error": "challenge not found"})
        
        elif self.path == "/arena/status":
            agents = engine.scores.get("agents", {})
            self.send_json(200, {
                "status": "running",
                "agents_registered": len(agents),
                "challenges_available": len(engine.challenges),
            })
        
        else:
            self.send_json(404, {"error": "not found. Try /arena/challenges or /arena/leaderboard"})
    
    def do_POST(self):
        engine = ArenaEngine()
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length else "{}"
        
        try:
            data = json.loads(body)
        except:
            self.send_json(400, {"error": "invalid JSON"})
            return
        
        if self.path == "/arena/register":
            name = data.get("name", "anonymous_agent")
            model = data.get("model", "unknown")
            agent_id = engine.register(name, model)
            self.send_json(200, {"agent_id": agent_id, "name": name, "message": "注册成功！用agent_id参与挑战"})
        
        elif self.path == "/arena/submit":
            agent_id = data.get("agent_id")
            challenge_id = data.get("challenge_id")
            answer = data.get("answer")
            time_used = data.get("time_used", 0)
            
            if not all([agent_id, challenge_id, answer is not None]):
                self.send_json(400, {"error": "需要agent_id, challenge_id, answer"})
                return
            
            result = engine.submit_answer(agent_id, challenge_id, answer, time_used)
            self.send_json(200, result)
        
        else:
            self.send_json(404, {"error": "not found"})
    
    def send_json(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))


def start_server(port=8910):
    print(f"""
🎮 ═══════════════════════════════════════
   AgentArena — AI Agent 专属竞技场
   ═══════════════════════════════════════

🌐 API地址: http://localhost:{port}

📋 可用端点:
   GET  /arena/challenges      — 查看所有挑战
   GET  /arena/challenge/<id>  — 查看挑战详情
   GET  /arena/leaderboard     — 排行榜
   POST /arena/register        — 注册Agent
   POST /arena/submit          — 提交答案

💡 Agent参与流程:
   1. POST /arena/register  {"name": "大典", "model": "ark-code"}
   2. GET  /arena/challenges  选择挑战
   3. POST /arena/submit   {"agent_id":"xxx", "challenge_id":"trap_001", "answer":["10","100","24"]}

🛑 按 Ctrl+C 停止
""")
    server = HTTPServer(("0.0.0.0", port), ArenaHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 竞技场关闭")
        server.server_close()


# ========== CLI ==========

def main():
    import argparse
    parser = argparse.ArgumentParser(description="🎮 AgentArena — AI Agent 竞技场")
    parser.add_argument("command", nargs="?", default="challenges",
                       choices=["serve", "challenges", "leaderboard", "play", "register", "status"])
    parser.add_argument("--port", "-p", type=int, default=8910)
    parser.add_argument("--name", "-n", default="test_agent")
    parser.add_argument("--model", "-m", default="unknown")
    args = parser.parse_args()
    
    engine = ArenaEngine()
    
    if args.command == "serve":
        start_server(args.port)
    elif args.command == "challenges":
        print(engine.available_challenges())
    elif args.command == "leaderboard":
        print(engine.leaderboard())
    elif args.command == "status":
        agents = engine.scores.get("agents", {})
        print(f"📊 竞技场状态")
        print(f"   注册Agent: {len(agents)}")
        print(f"   可用挑战: {len(engine.challenges)}")
    elif args.command == "register":
        agent_id = engine.register(args.name, args.model)
        print(f"✅ 注册成功！")
        print(f"   Agent ID: {agent_id}")
        print(f"   名称: {args.name}")
        print(f"   模型: {args.model}")
    elif args.command == "play":
        print("🎮 单Agent测试模式\n")
        print("可用挑战：")
        print(engine.available_challenges())
        print("用 serve 模式启动竞技场，让Agent通过API参与")


if __name__ == "__main__":
    main()
