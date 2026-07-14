请执行KET语法专项训练（暑假模式，对标真实考试）。

⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取 KET备考计划.md → 确认当前阶段（第二阶段·能力提升）
2. 读取 data/error_book.json → 筛选英语语法错题

按当天日期取模轮换语法点：
- 日期%6==0：一般现在时 vs 现在进行时
- 日期%6==1：一般过去时（规则+不规则动词）
- 日期%6==2：一般将来时（will / be going to）
- 日期%6==3：情态动词（can / must / should）
- 日期%6==4：比较级/最高级
- 日期%6==5：条件句（if）+ 复合句

出题（6题，逐题生成发送）：

⚠️⚠️⚠️ 【核心流程·逐题发送】每次只生成并发送1道题！

```
① 语法讲解（1条消息）
   - 用 call_llm 生成：简短讲解当天语法点规则+2个例句
   - 用 send_feishu(msg_type="text") 发送

② 逐题出题（6道题，每题1条消息）
   - 对第1题：用 call_llm 生成 → send_feishu(msg_type="text") 发送
   - 对第2题：用 call_llm 生成 → send_feishu(msg_type="text") 发送
   - ...依次生成第3、4、5、6题

③ 组装结果 write_file 存入 {output_file}
```

每次 call_llm 的 prompt：
"请生成一道KET语法题（只生成1道题）：
语法点：{当天语法点}
题型：填空/选择/改错/句型转换（随机轮换）
词汇范围：KET核心词表 ~1500词
输出JSON格式：{{"id":"","question":"完整题目+选项","answer":"","hint":"","ket_part":"","topic_id":""}}
⚠️ question字段必须包含完整题目，填空用______（6个下划线），选项用A/B/C"

⚠️ 格式要求：
- 语法点不能超出A2范围（禁止虚拟语气、被动语态、现在完成时）
- 选项用A/B/C
- 填空题原文完整，空格用______（6个下划线），选项列在题号下方
- 改错题完整原文，每行标注行号
- 用 send_feishu(msg_type="text") 发送，禁止用卡片消息

最终存入格式：{{"test_id":"{test_id}","date":"{today_str}","type":"ket_grammar","questions":[6道题]}}

⚠️ 铁律：每次只生成1道题，发完再生成下一道！