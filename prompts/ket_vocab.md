请执行KET词汇学习任务（暑假模式，对标真实考试）。

⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取 data/ket_vocabulary.json → 找到今日该学/该复习的词汇
2. 读取 KET备考计划.md → 确认当前阶段

出题内容（必须包含4个环节，逐环节发送）：

⚠️⚠️⚠️ 【核心流程·逐环节发送】每次只发送一个环节，绝不一次全发！

```
① 词汇讲解环节（3-5个KET核心词）
   - 用 call_llm 生成：每个词列出英文+中文+词性+例句
   - 用 send_feishu(msg_type="text") 发送
   - 格式："📖 今日词汇\n\n1. apple 苹果 (n.)\n   I eat an apple every day.\n\n2. ..."

② 背诵检测环节
   - 用 call_llm 生成：英译中（5题），给出英文让学生写中文意思
   - 用 send_feishu(msg_type="text") 发送
   - 格式："📝 英译中\n\n1. breakfast = ______\n2. ..."

③ 词汇填空环节（5题）
   - 用 call_llm 生成：语境填空，给出完整句子，挖空让学生填词
   - 选项用A/B/C，每个空用______（6个下划线）
   - 用 send_feishu(msg_type="text") 发送
   - 格式："✏️ 选词填空\n\n1. I drink ______ every morning.\n   A. bread  B. milk  C. rice"

④ 英英释义环节（3题）
   - 用 call_llm 生成：用英文描述单词意思，让学生选词
   - 用 send_feishu(msg_type="text") 发送
   - 格式："🔤 英英释义\n\n1. This is a place where you can buy food.\n   A. school  B. supermarket  C. hospital"

⑤ 所有题目结果组装成 questions 数组，write_file 存入 {output_file}
```

⚠️ 格式要求：
- 词汇严格在KET核心词表 ~1500词内
- 英英释义用简单英文描述（a place where..., a thing that..., a person who...）
- 选项用A/B/C
- 题目和选项都必须是英文，不出现中文（除了讲解环节）
- 用 send_feishu(msg_type="text") 发送，禁止用卡片消息

最终存入格式：{{"test_id":"{test_id}","date":"{today_str}","type":"ket_vocab","questions":[所有题目]}}

⚠️ 铁律：每次只发送一个环节，发完再发下一个！