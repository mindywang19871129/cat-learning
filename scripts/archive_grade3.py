#!/usr/bin/env python3
"""
三年级数据归档脚本
==================
扫描 data/ 目录，生成三年级学习总结、错题规律、掌握度报告。
归档到 data/archive/grade3_2026_spring/
"""
import json
import sys
from pathlib import Path
from datetime import datetime
from collections import Counter

HOME = Path(__file__).parent.parent.resolve()
DATA_DIR = HOME / "data"
ARCHIVE_DIR = DATA_DIR / "archive" / "grade3_2026_spring"

def main():
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    
    # 1. 汇总所有题目
    questions_dir = DATA_DIR / "questions"
    all_questions = []
    if questions_dir.exists():
        for f in sorted(questions_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                qs = data.get("questions", data.get("math", []) + data.get("english", []) + data.get("vocab", []))
                for q in qs:
                    q["_source_file"] = f.name
                    q["_test_id"] = data.get("test_id", "")
                    q["_date"] = data.get("date", "")
                all_questions.extend(qs)
            except Exception:
                pass
    
    # 也读 today_questions.json
    today_file = DATA_DIR / "today_questions.json"
    if today_file.exists():
        try:
            data = json.loads(today_file.read_text(encoding="utf-8"))
            qs = data.get("questions", data.get("math", []) + data.get("english", []) + data.get("vocab", []))
            for q in qs:
                q["_source_file"] = "today_questions.json"
                q["_test_id"] = data.get("test_id", "")
                q["_date"] = data.get("date", "")
            all_questions.extend(qs)
        except Exception:
            pass
    
    # 2. 统计
    total = len(all_questions)
    graded = [q for q in all_questions if "score" in q or "batch_id" in q]
    correct = [q for q in graded if q.get("score", 0) >= 1 or q.get("is_correct")]
    wrong = [q for q in graded if q.get("score", 0) == 0 or q.get("is_correct") == False]
    
    # 3. 错题分析
    error_file = DATA_DIR / "error_book.json"
    errors = []
    if error_file.exists():
        try:
            errors = json.loads(error_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    
    error_types = Counter(e.get("error_type", "未知") for e in errors)
    
    # 4. 掌握度
    mastery_file = DATA_DIR / "mastery.json"
    mastery = {}
    if mastery_file.exists():
        try:
            mastery_data = json.loads(mastery_file.read_text(encoding="utf-8"))
            if isinstance(mastery_data, list):
                for m in mastery_data:
                    mastery[m.get("topic_id", m.get("name", ""))] = m.get("score", 0)
            elif isinstance(mastery_data, dict):
                mastery = mastery_data
        except Exception:
            pass
    
    # 5. 生成总结报告
    report_lines = [
        f"# 三年级下学期学习总结",
        f"",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"教材：北师大版 三年级下学期",
        f"",
        f"## 题目统计",
        f"- 总出题数：{total}",
        f"- 已批改：{len(graded)}",
        f"- 正确：{len(correct)}",
        f"- 错误：{len(wrong)}",
        f"- 正确率：{len(correct) / len(graded) * 100:.1f}%" if graded else "- 正确率：暂无数据",
        f"",
        f"## 错题统计",
        f"- 错题总数：{len(errors)}",
        f"",
    ]
    
    if error_types:
        report_lines.append("### 错误类型分布")
        for etype, count in error_types.most_common():
            report_lines.append(f"- {etype}：{count}次")
    
    report_lines.extend([
        f"",
        f"## 掌握度概览",
    ])
    
    if mastery:
        if isinstance(mastery, dict):
            scores = list(mastery.values()) if all(isinstance(v, (int, float)) for v in mastery.values()) else []
            if scores:
                mastered = sum(1 for s in scores if s >= 95)
                learning = sum(1 for s in scores if 70 <= s < 95)
                weak = sum(1 for s in scores if 50 <= s < 70)
                new = sum(1 for s in scores if s < 50)
                report_lines.extend([
                    f"- 已掌握（≥95）：{mastered}",
                    f"- 学习中（70-94）：{learning}",
                    f"- 薄弱（50-69）：{weak}",
                    f"- 未开始（<50）：{new}",
                ])
    
    report_lines.extend([
        f"",
        f"## 四年级衔接建议",
        f"- 保留计算粗心类、单位换算类、审题偏差类错题记录",
        f"- 图形/周长基础薄弱点需在四年级继续关注",
        f"- 建议四年级重新建立 mastery.json，保留三年级总结作为参考",
    ])
    
    report_path = ARCHIVE_DIR / "summary.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"✅ 总结报告已生成: {report_path}")
    
    # 6. 保存题目索引
    index = {
        "generated_at": datetime.now().isoformat(),
        "total_questions": total,
        "graded": len(graded),
        "correct": len(correct),
        "wrong": len(wrong),
        "error_count": len(errors),
        "error_types": dict(error_types),
        "questions": [{"id": q.get("id", ""), "test_id": q.get("_test_id", ""), 
                       "date": q.get("_date", ""), "question": q.get("question", "")[:100],
                       "score": q.get("score"), "is_correct": q.get("is_correct")}
                      for q in all_questions],
    }
    index_path = ARCHIVE_DIR / "questions_index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 题目索引已生成: {index_path}")
    
    # 7. 保存错题总结
    error_summary = {
        "generated_at": datetime.now().isoformat(),
        "total_errors": len(errors),
        "error_types": dict(error_types),
        "errors": errors,
    }
    error_summary_path = ARCHIVE_DIR / "error_summary.json"
    error_summary_path.write_text(json.dumps(error_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 错题总结已生成: {error_summary_path}")
    
    # 8. 保存掌握度快照
    mastery_snapshot_path = ARCHIVE_DIR / "mastery_snapshot.json"
    mastery_snapshot_path.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "mastery": mastery,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ 掌握度快照已生成: {mastery_snapshot_path}")
    
    print(f"\n📦 归档完成！文件位于: {ARCHIVE_DIR}")
    print(f"   总题目: {total} | 已批改: {len(graded)} | 错题: {len(errors)}")

if __name__ == "__main__":
    main()
