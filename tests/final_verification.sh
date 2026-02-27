#!/bin/bash

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║              🎉 最终验证 - 所有功能检查                            ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

passed=0
failed=0

# 测试函数
test_item() {
    local name="$1"
    local command="$2"
    
    echo -n "  测试: $name ... "
    if eval "$command" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC}"
        ((passed++))
        return 0
    else
        echo -e "${RED}✗${NC}"
        ((failed++))
        return 1
    fi
}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1. 代码语法检查"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
test_item "db/database.py" "python -m py_compile db/database.py"
test_item "core/transcriber.py" "python -m py_compile core/transcriber.py"
test_item "app.py" "python -m py_compile app.py"
test_item "utils/copy_button.py" "python -m py_compile utils/copy_button.py"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2. 模块导入检查"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
test_item "数据库模块" "python -c 'from db.database import update_transcription_progress, get_transcription_progress, assemble_partial_transcript'"
test_item "转写模块" "python -c 'from core.transcriber import audio_to_text'"
test_item "复制按钮工具" "python -c 'from utils.copy_button import create_task_copy_button, create_copy_button_with_tooltip'"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3. 数据库功能检查"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
test_item "数据库初始化" "python -c 'from db.database import init_db; import tempfile; from pathlib import Path; f = tempfile.NamedTemporaryFile(suffix=\".db\", delete=False); init_db(f.name); Path(f.name).unlink()'"
test_item "进度字段存在" "python -c 'from db.database import init_db, get_connection; import tempfile; from pathlib import Path; f = tempfile.NamedTemporaryFile(suffix=\".db\", delete=False); init_db(f.name); conn = get_connection(f.name).__enter__(); cursor = conn.execute(\"PRAGMA table_info(tasks)\"); cols = {r[\"name\"] for r in cursor.fetchall()}; assert \"transcription_progress\" in cols; Path(f.name).unlink()'"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "4. 工具函数检查"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
test_item "生成复制按钮" "python -c 'from utils.copy_button import create_task_copy_button; html = create_task_copy_button(1, \"test\"); assert len(html) > 0'"
test_item "自定义参数" "python -c 'from utils.copy_button import create_copy_button_with_tooltip; html = create_copy_button_with_tooltip(\"test\", \"text\", button_color=\"#00ff00\"); assert \"#00ff00\" in html'"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "5. 文件完整性检查"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
test_item "核心代码文件" "test -f db/database.py && test -f core/transcriber.py && test -f app.py && test -f utils/copy_button.py"
test_item "测试脚本" "test -f test_progress_feature.py && test -f test_end_to_end.py && test -f test_copy_button_refactor.py"
test_item "文档文件" "test -f FINAL_SUMMARY.md && test -f COPY_BUTTON_REFACTOR_REPORT.md && test -f QUICK_REFERENCE.txt"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "6. 代码质量检查"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
test_item "无内联 HTML" "! grep -q 'copy_button_html = f\"\"\"' app.py"
test_item "使用工具函数" "grep -q 'create_task_copy_button' app.py"
test_item "导入工具模块" "grep -q 'from utils.copy_button import' app.py"

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║                        验证结果                                    ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "  通过: ${GREEN}$passed${NC}"
echo "  失败: ${RED}$failed${NC}"
echo "  总计: $((passed + failed))"
echo ""

if [ $failed -eq 0 ]; then
    echo -e "${GREEN}✓ 所有验证通过！${NC}"
    echo ""
    echo "🎉 项目已准备就绪，可以部署使用！"
    echo ""
    echo "快速启动："
    echo "  $ streamlit run app.py"
    echo ""
    exit 0
else
    echo -e "${RED}✗ 部分验证失败${NC}"
    echo ""
    echo "请检查失败的项目并修复。"
    echo ""
    exit 1
fi
