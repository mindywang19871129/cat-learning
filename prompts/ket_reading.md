请执行KET阅读真题训练（暑假模式，对标真实考试）。

⚠️⚠️⚠️ 严禁调用 send_feishu！系统已被配置为拒绝所有 send_feishu 调用！

test_id = "{test_id}"
每道题用 _gen_question_id() 生成全局唯一编号

1. 读取 KET备考计划.md → 确认当前阶段（第二阶段·能力提升）
2. 读取 data/ket_vocabulary.json → 确保词汇在KET词表内
3. 读取 data/error_book.json → 筛选英语错题

出题（6题，按KET真题格式，每天轮换题型组合）：

按当天日期取模轮换：
- 日期%3==0：短信息匹配3题(Part1) + 长文选择3题(Part3)
- 日期%3==1：短文匹配3题(Part2) + 完形填空3题(Part4)
- 日期%3==2：开放式完形3题(Part5) + 长文选择3题(Part3)

⚠️ KET真题格式要求：
- 题材：邮件、通知、广告、短文、故事（KET真实场景）
- 词汇：严格KET词表(~1500词)，不用超纲词
- 选项：用A/B/C，不要用1/2/3
- 短文长度：50-150词
- 完形填空：原文完整，空格用______（6个下划线），选项列在题号下方
- 每道题标注对应KET真题Part编号

用 write_file 存入 {output_file}
格式：{{"test_id":"{test_id}","date":"{today_str}","type":"ket_reading","questions":[{{id,question,answer,hint,ket_part:"Part1/2/3/4/5",topic_id}}]}}

⚠️ 只存储，不要调用 send_feishu！（已被系统禁用）