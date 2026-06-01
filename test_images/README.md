# 测试图片目录

用于测试图片识别功能的测试图片。

## 建议的测试图片

1. **手写数学题答案**:
   - 小学生手写的数学计算题
   - 例如: "53 ÷ 8 = 6...5"
   - 文件名: `math_answer.jpg`

2. **手写英文答案**:
   - 小学生手写的英文单词或句子
   - 例如: "visited, cooked, played"
   - 文件名: `english_answer.jpg`

3. **打印体题目**:
   - 打印的数学或英文题目
   - 用于测试印刷体识别
   - 文件名: `printed_question.jpg`

## 如何获取测试图片

### 方法1: 使用现有图片
将现有的手写或打印图片复制到本目录。

### 方法2: 生成测试图片
可以使用以下Python代码生成简单的测试图片:

```python
from PIL import Image, ImageDraw, ImageFont
import os

# 创建测试图片目录
os.makedirs("test_images", exist_ok=True)

# 创建手写数学答案图片
img = Image.new('RGB', (400, 200), color='white')
draw = ImageDraw.Draw(img)

# 模拟手写文字
draw.text((50, 50), "53 ÷ 8 = 6...5", fill='black', font=None)
draw.text((50, 100), "小明有60张邮票", fill='black', font=None)
draw.text((50, 150), "小红有20张邮票", fill='black', font=None)

img.save("test_images/math_answer.jpg")
print("测试图片已生成")
```

### 方法3: 使用截图
1. 在纸上手写答案并拍照
2. 截图保存为jpg/png格式
3. 放入本目录

## 运行测试

```bash
# 设置环境变量
export DEEPSEEK_API_KEY=your_api_key_here
export CATLEARN_HOME=$(pwd)

# 运行测试
python test_ocr.py
```

## 预期结果

测试脚本应该能够:
1. 成功识别图片中的文字
2. 显示使用的识别引擎（LLM视觉或传统OCR）
3. 显示识别置信度
4. 输出识别结果

## 故障排除

如果测试失败:
1. 检查环境变量是否正确设置
2. 检查图片文件是否存在且可读
3. 检查网络连接（LLM视觉需要API访问）
4. 检查图片大小（不超过10MB）