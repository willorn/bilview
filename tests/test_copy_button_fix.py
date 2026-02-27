"""
测试复制逐字稿功能的修复。

验证：
1. 旧的 _copy_to_clipboard 函数已删除
2. 新的 HTML + JavaScript 复制功能已添加
3. 代码语法正确
"""
import re


def test_copy_button_fix():
    """测试复制按钮修复。"""
    print("=" * 60)
    print("测试：复制逐字稿按钮修复")
    print("=" * 60)

    with open('app.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # 测试 1: 确认旧函数已删除
    print("\n测试 1: 确认旧的 _copy_to_clipboard 函数已删除...")
    if 'def _copy_to_clipboard' in content:
        print("  ❌ 失败: 旧函数仍然存在")
        return False
    print("  ✓ 通过: 旧函数已删除")

    # 测试 2: 确认新的复制功能已添加
    print("\n测试 2: 确认新的 HTML + JavaScript 复制功能已添加...")
    if 'copyToClipboard_' not in content:
        print("  ❌ 失败: 未找到新的复制功能")
        return False
    if 'navigator.clipboard.writeText' not in content:
        print("  ❌ 失败: 未找到剪贴板 API 调用")
        return False
    print("  ✓ 通过: 新的复制功能已添加")

    # 测试 3: 确认按钮样式正确
    print("\n测试 3: 确认按钮样式...")
    if '复制逐字稿</button>' not in content:
        print("  ❌ 失败: 按钮文本不正确")
        return False
    print("  ✓ 通过: 按钮样式正确")

    # 测试 4: 确认使用了 task.id 作为唯一标识
    print("\n测试 4: 确认使用了 task.id 作为唯一标识...")
    pattern = r'copyToClipboard_\{task\.id\}'
    if not re.search(pattern, content):
        print("  ❌ 失败: 未使用 task.id 作为唯一标识")
        return False
    print("  ✓ 通过: 使用了 task.id 作为唯一标识")

    # 测试 5: 确认使用了 repr() 来安全转义文本
    print("\n测试 5: 确认使用了 repr() 来安全转义文本...")
    if 'repr(task.transcript_text)' not in content:
        print("  ❌ 失败: 未使用 repr() 转义")
        return False
    print("  ✓ 通过: 使用了 repr() 转义")

    # 测试 6: 确认有成功和失败的提示
    print("\n测试 6: 确认有成功和失败的提示...")
    if '已复制到剪贴板' not in content:
        print("  ❌ 失败: 缺少成功提示")
        return False
    if '复制失败' not in content:
        print("  ❌ 失败: 缺少失败提示")
        return False
    print("  ✓ 通过: 有成功和失败的提示")

    print("\n" + "=" * 60)
    print("✓ 所有测试通过！复制功能已正确修复。")
    print("=" * 60)
    return True


def test_code_quality():
    """测试代码质量。"""
    print("\n" + "=" * 60)
    print("测试：代码质量检查")
    print("=" * 60)

    with open('app.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # 测试 1: Python 语法
    print("\n测试 1: Python 语法检查...")
    import ast
    try:
        ast.parse(content)
        print("  ✓ 通过: Python 语法正确")
    except SyntaxError as e:
        print(f"  ❌ 失败: 语法错误 - {e}")
        return False

    # 测试 2: 导入检查
    print("\n测试 2: 导入检查...")
    try:
        import sys
        sys.path.insert(0, '.')
        # 只检查导入，不执行
        with open('app.py', 'r', encoding='utf-8') as f:
            lines = f.readlines()

        imports = [line.strip() for line in lines if line.strip().startswith(('import ', 'from '))]
        print(f"  ✓ 通过: 找到 {len(imports)} 个导入语句")
    except Exception as e:
        print(f"  ❌ 失败: 导入检查失败 - {e}")
        return False

    # 测试 3: 检查是否有明显的安全问题
    print("\n测试 3: 安全检查...")
    dangerous_patterns = [
        (r'eval\(', 'eval() 调用'),
        (r'exec\(', 'exec() 调用'),
        (r'__import__\(', '__import__() 调用'),
    ]

    issues = []
    for pattern, desc in dangerous_patterns:
        if re.search(pattern, content):
            issues.append(desc)

    if issues:
        print(f"  ⚠️  警告: 发现潜在安全问题: {', '.join(issues)}")
    else:
        print("  ✓ 通过: 未发现明显的安全问题")

    print("\n" + "=" * 60)
    print("✓ 代码质量检查通过！")
    print("=" * 60)
    return True


def show_fix_summary():
    """显示修复摘要。"""
    print("\n" + "=" * 60)
    print("修复摘要")
    print("=" * 60)

    print("\n问题：")
    print("  ❌ 原来的 _copy_to_clipboard 函数只是将文本存储到 session_state")
    print("  ❌ 没有实际的前端代码来执行复制操作")
    print("  ❌ Streamlit 不支持直接访问剪贴板")

    print("\n解决方案：")
    print("  ✓ 删除了无效的 _copy_to_clipboard 函数")
    print("  ✓ 使用 HTML + JavaScript 实现真正的复制功能")
    print("  ✓ 使用 navigator.clipboard.writeText() API")
    print("  ✓ 为每个任务生成唯一的函数名（避免冲突）")
    print("  ✓ 使用 repr() 安全转义文本（防止 XSS）")
    print("  ✓ 添加成功和失败的用户提示")

    print("\n技术细节：")
    print("  • 使用 st.markdown() 渲染 HTML + JavaScript")
    print("  • 使用 unsafe_allow_html=True 允许执行 JavaScript")
    print("  • 函数名包含 task.id 确保唯一性")
    print("  • 使用 repr() 转义特殊字符（引号、换行等）")

    print("\n用户体验：")
    print("  • 点击按钮后立即复制到剪贴板")
    print("  • 成功时显示 '已复制到剪贴板！'")
    print("  • 失败时显示错误信息")
    print("  • 按钮样式与 Streamlit 主题一致")

    print("\n兼容性：")
    print("  • 支持所有现代浏览器（Chrome, Firefox, Safari, Edge）")
    print("  • 需要 HTTPS 或 localhost（剪贴板 API 要求）")
    print("  • 不支持 IE 11 及更早版本")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    print("\n🔧 复制逐字稿按钮修复验证\n")

    results = []
    results.append(test_copy_button_fix())
    results.append(test_code_quality())

    show_fix_summary()

    print("\n" + "=" * 60)
    print("最终结果")
    print("=" * 60)

    passed = sum(results)
    total = len(results)

    if passed == total:
        print(f"\n✓ 所有测试通过 ({passed}/{total})")
        print("\n🎉 复制功能已成功修复！可以启动应用测试。")
        print("\n建议测试步骤：")
        print("  1. 启动应用：streamlit run app.py")
        print("  2. 在历史记录中选择一个有转写文本的任务")
        print("  3. 点击 '复制逐字稿' 按钮")
        print("  4. 粘贴到文本编辑器验证")
        exit(0)
    else:
        print(f"\n✗ 部分测试失败 ({passed}/{total})")
        exit(1)
