#!/usr/bin/env python3
"""
测试增强的图片识别功能
"""
import os
import sys
import json
from pathlib import Path

# 设置环境变量
os.environ["CATLEARN_HOME"] = str(Path(__file__).parent.resolve())

# 导入核心模块
sys.path.insert(0, os.environ["CATLEARN_HOME"])

from core import enhanced_ocr_image, analyze_image_via_llm

def test_ocr_functionality():
    """测试OCR功能"""
    print("=== 测试增强的图片识别功能 ===\n")
    
    # 检查是否有测试图片
    test_images_dir = Path("test_images")
    if not test_images_dir.exists():
        print(f"❌ 测试图片目录不存在: {test_images_dir}")
        print("请创建 test_images/ 目录并放入测试图片")
        return
    
    test_images = list(test_images_dir.glob("*.jpg")) + list(test_images_dir.glob("*.png")) + list(test_images_dir.glob("*.jpeg"))
    
    if not test_images:
        print("❌ 没有找到测试图片")
        print("请在 test_images/ 目录中放入jpg/png格式的图片")
        return
    
    print(f"找到 {len(test_images)} 张测试图片")
    
    for img_path in test_images[:3]:  # 测试前3张
        print(f"\n--- 测试图片: {img_path.name} ---")
        
        # 测试增强OCR
        print("1. 测试 enhanced_ocr_image (整合LLM视觉+传统OCR):")
        result = enhanced_ocr_image(str(img_path))
        data = json.loads(result)
        
        if data.get("success"):
            print(f"   ✅ 成功! 引擎: {data.get('engine', 'unknown')}")
            print(f"   置信度: {data.get('confidence_hint', 'unknown')}")
            print(f"   识别行数: {data.get('line_count', 0)}")
            print(f"   识别文本: {data.get('text', '')[:200]}...")
        else:
            print(f"   ❌ 失败: {data.get('error', 'unknown error')}")
        
        # 测试纯LLM视觉
        print("\n2. 测试 analyze_image_via_llm (纯LLM视觉):")
        result = analyze_image_via_llm(str(img_path), "请识别图片中的文字内容")
        data = json.loads(result)
        
        if data.get("success"):
            print(f"   ✅ 成功! 引擎: {data.get('engine', 'unknown')}")
            print(f"   分析结果: {data.get('analysis', '')[:200]}...")
        else:
            print(f"   ❌ 失败: {data.get('error', 'unknown error')}")

def test_session_context():
    """测试会话上下文功能"""
    print("\n=== 测试会话上下文功能 ===\n")
    
    from core import init_new_session, run
    
    # 创建测试会话
    session = init_new_session()
    
    # 模拟第一次交互
    print("1. 第一次交互: 询问姓名")
    result1 = run("你好，我是小明，你叫什么名字？", session)
    print(f"   回答: {result1[:100] if result1 else '无回答'}")
    
    # 模拟第二次交互（应该记住上下文）
    print("\n2. 第二次交互: 再次询问")
    result2 = run("你刚才说你是谁？", session)
    print(f"   回答: {result2[:100] if result2 else '无回答'}")
    
    # 检查是否记住了上下文
    if result2 and "小明" in result2:
        print("   ✅ 成功记住了上下文!")
    else:
        print("   ⚠️ 可能没有记住上下文")

def main():
    """主测试函数"""
    print("小肥猫学习助手 - 功能测试\n")
    
    # 检查环境变量
    required_env_vars = ["DEEPSEEK_API_KEY"]
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
    
    if missing_vars:
        print(f"❌ 缺少环境变量: {missing_vars}")
        print("请设置以下环境变量:")
        for var in missing_vars:
            print(f"  export {var}=your_value")
        return
    
    print("✅ 环境变量检查通过")
    
    # 运行测试
    test_ocr_functionality()
    test_session_context()
    
    print("\n=== 测试完成 ===")

if __name__ == "__main__":
    main()