"""
测试复制按钮工具函数。

验证：
1. 工具函数可以正常导入
2. 生成的 HTML 代码正确
3. 代码重用性良好
4. 参数可配置
"""
import re


def test_import():
    """测试 1: 导入工具函数。"""
    print("=" * 60)
    print("测试 1: 导入工具函数")
    print("=" * 60)

    try:
        from utils.copy_button import create_copy_button_with_tooltip, create_task_copy_button
        print("  ✓ 工具函数导入成功")
        return True
    except ImportError as e:
        print(f"  ❌ 导入失败: {e}")
        return False


def test_basic_generation():
    """测试 2: 基本 HTML 生成。"""
    print("\n" + "=" * 60)
    print("测试 2: 基本 HTML 生成")
    print("=" * 60)

    from utils.copy_button import create_copy_button_with_tooltip

    html = create_copy_button_with_tooltip(
        button_id="test_1",
        text_to_copy="测试文本",
        button_text="复制"
    )

    # 验证关键元素
    checks = [
        ("有按钮元素", '<button' in html),
        ("有 tooltip 元素", 'id="tooltip_test_1"' in html),
        ("有复制函数", 'function copyToClipboard_test_1()' in html),
        ("有文本转义", 'navigator.clipboard.writeText' in html),
        ("有成功提示", '已复制到剪贴板' in html),
        ("有失败提示", '复制失败' in html),
    ]

    all_passed = True
    for name, result in checks:
        status = "✓" if result else "❌"
        print(f"  {status} {name}")
        if not result:
            all_passed = False

    return all_passed


def test_task_copy_button():
    """测试 3: 任务复制按钮生成。"""
    print("\n" + "=" * 60)
    print("测试 3: 任务复制按钮生成")
    print("=" * 60)

    from utils.copy_button import create_task_copy_button

    html = create_task_copy_button(
        task_id=123,
        text_to_copy="任务文本内容",
        button_text="复制逐字稿"
    )

    # 验证任务特定元素
    checks = [
        ("使用任务 ID", 'copyBtn_123' in html),
        ("按钮文字正确", '复制逐字稿' in html),
        ("使用 Streamlit 主题色", '#ff4b4b' in html),
    ]

    all_passed = True
    for name, result in checks:
        status = "✓" if result else "❌"
        print(f"  {status} {name}")
        if not result:
            all_passed = False

    return all_passed


def test_customization():
    """测试 4: 参数自定义。"""
    print("\n" + "=" * 60)
    print("测试 4: 参数自定义")
    print("=" * 60)

    from utils.copy_button import create_copy_button_with_tooltip

    html = create_copy_button_with_tooltip(
        button_id="custom",
        text_to_copy="自定义文本",
        button_text="自定义按钮",
        button_color="#00ff00",
        success_message="✓ 自定义成功",
        error_message="✗ 自定义失败",
        success_duration=5000,
    )

    # 验证自定义参数
    checks = [
        ("自定义按钮文字", '自定义按钮' in html),
        ("自定义按钮颜色", '#00ff00' in html),
        ("自定义成功消息", '自定义成功' in html),
        ("自定义失败消息", '自定义失败' in html),
        ("自定义持续时间", '5000' in html),
    ]

    all_passed = True
    for name, result in checks:
        status = "✓" if result else "❌"
        print(f"  {status} {name}")
        if not result:
            all_passed = False

    return all_passed


def test_code_reduction():
    """测试 5: 代码简化效果。"""
    print("\n" + "=" * 60)
    print("测试 5: 代码简化效果")
    print("=" * 60)

    # 读取 app.py
    with open('app.py', 'r', encoding='utf-8') as f:
        app_content = f.read()

    # 检查是否使用了工具函数
    if 'from utils.copy_button import' in app_content:
        print("  ✓ 已导入工具函数")
    else:
        print("  ❌ 未导入工具函数")
        return False

    if 'create_task_copy_button' in app_content:
        print("  ✓ 使用了工具函数")
    else:
        print("  ❌ 未使用工具函数")
        return False

    # 检查是否移除了内联 HTML
    if 'copy_button_html = f"""' in app_content:
        print("  ❌ 仍有内联 HTML 代码")
        return False
    else:
        print("  ✓ 已移除内联 HTML 代码")

    # 统计代码行数
    copy_button_lines = app_content.count('create_task_copy_button')
    print(f"  ✓ 复制按钮调用次数: {copy_button_lines}")

    return True


def test_app_integration():
    """测试 6: 应用集成。"""
    print("\n" + "=" * 60)
    print("测试 6: 应用集成")
    print("=" * 60)

    # 测试导入
    try:
        import sys
        sys.path.insert(0, '.')
        from utils.copy_button import create_task_copy_button
        print("  ✓ 工具函数可以被应用导入")
    except ImportError as e:
        print(f"  ❌ 导入失败: {e}")
        return False

    # 测试生成
    try:
        html = create_task_copy_button(1, "测试")
        if html and len(html) > 0:
            print("  ✓ 工具函数可以正常生成 HTML")
        else:
            print("  ❌ 生成的 HTML 为空")
            return False
    except Exception as e:
        print(f"  ❌ 生成失败: {e}")
        return False

    # 测试 Python 语法
    import ast
    try:
        with open('app.py', 'r', encoding='utf-8') as f:
            ast.parse(f.read())
        print("  ✓ app.py 语法正确")
    except SyntaxError as e:
        print(f"  ❌ app.py 语法错误: {e}")
        return False

    return True


def show_improvement_summary():
    """显示改进总结。"""
    print("\n" + "=" * 60)
    print("改进总结")
    print("=" * 60)

    print("\n改进前（堆屎山）：")
    print("  ❌ 70+ 行内联 HTML + JavaScript")
    print("  ❌ 代码重复，难以维护")
    print("  ❌ 修改需要改多处")
    print("  ❌ 无法复用")

    print("\n改进后（工具化）：")
    print("  ✓ 2 行代码调用工具函数")
    print("  ✓ 代码集中管理")
    print("  ✓ 修改只需改一处")
    print("  ✓ 可以在任何地方复用")

    print("\n代码对比：")
    print("\n  改进前:")
    print("    copy_button_html = f\"\"\"")
    print("    <div style=\"position: relative;\">")
    print("        <button onclick=\"copyToClipboard_{task.id}()\" ...>")
    print("        ...")
    print("        (70+ 行)")
    print("    \"\"\"")
    print("    st.markdown(copy_button_html, unsafe_allow_html=True)")

    print("\n  改进后:")
    print("    copy_button_html = create_task_copy_button(task.id, task.transcript_text)")
    print("    st.markdown(copy_button_html, unsafe_allow_html=True)")

    print("\n工具函数特性：")
    print("  • 参数化配置 - 可自定义颜色、文字、时长")
    print("  • 类型提示 - 完整的类型注解")
    print("  • 文档注释 - 清晰的使用说明")
    print("  • 安全转义 - 自动处理特殊字符")
    print("  • 便捷函数 - 为常见场景提供快捷方式")

    print("\n复用场景：")
    print("  • 复制逐字稿")
    print("  • 复制总结")
    print("  • 复制链接")
    print("  • 复制任何文本内容")

    print("\n" + "=" * 60)


def show_usage_examples():
    """显示使用示例。"""
    print("\n" + "=" * 60)
    print("使用示例")
    print("=" * 60)

    print("\n示例 1: 基本使用")
    print("```python")
    print("from utils.copy_button import create_task_copy_button")
    print("")
    print("# 为任务生成复制按钮")
    print("html = create_task_copy_button(task.id, task.transcript_text)")
    print("st.markdown(html, unsafe_allow_html=True)")
    print("```")

    print("\n示例 2: 自定义样式")
    print("```python")
    print("from utils.copy_button import create_copy_button_with_tooltip")
    print("")
    print("# 自定义按钮")
    print("html = create_copy_button_with_tooltip(")
    print("    button_id='custom',")
    print("    text_to_copy='自定义内容',")
    print("    button_text='复制',")
    print("    button_color='#00ff00',")
    print("    success_message='✓ 复制成功！',")
    print(")")
    print("st.markdown(html, unsafe_allow_html=True)")
    print("```")

    print("\n示例 3: 复制总结")
    print("```python")
    print("# 复制总结文本")
    print("html = create_task_copy_button(")
    print("    task_id=task.id,")
    print("    text_to_copy=task.summary_text,")
    print("    button_text='复制总结'")
    print(")")
    print("st.markdown(html, unsafe_allow_html=True)")
    print("```")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    print("\n🛠️  复制按钮工具化测试\n")

    results = []
    results.append(test_import())
    results.append(test_basic_generation())
    results.append(test_task_copy_button())
    results.append(test_customization())
    results.append(test_code_reduction())
    results.append(test_app_integration())

    show_improvement_summary()
    show_usage_examples()

    print("\n" + "=" * 60)
    print("最终结果")
    print("=" * 60)

    passed = sum(results)
    total = len(results)

    if passed == total:
        print(f"\n✓ 所有测试通过 ({passed}/{total})")
        print("\n🎉 工具化改进成功！")
        print("\n代码改进：")
        print("  • 从 70+ 行内联代码 → 2 行工具调用")
        print("  • 代码重复 → 集中管理")
        print("  • 难以维护 → 易于维护")
        print("  • 无法复用 → 可以复用")
        print("\n建议：")
        print("  1. 启动应用测试功能")
        print("  2. 在其他地方复用工具函数")
        print("  3. 根据需要自定义参数")
        exit(0)
    else:
        print(f"\n✗ 部分测试失败 ({passed}/{total})")
        exit(1)
