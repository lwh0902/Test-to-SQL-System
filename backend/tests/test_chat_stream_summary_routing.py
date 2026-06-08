from app.api.chat import _should_generate_analysis_answer


def test_chat_and_help_skip_analysis_summary_pass():
    assert _should_generate_analysis_answer("chat", has_data=False, has_plan=False) is False
    assert _should_generate_analysis_answer("help", has_data=False, has_plan=False) is False


def test_data_answers_still_use_analysis_summary_pass():
    assert _should_generate_analysis_answer("answer", has_data=False, has_plan=False) is True
    assert _should_generate_analysis_answer("answer", has_data=True, has_plan=False) is True


def test_plan_answers_skip_analysis_summary_pass():
    assert _should_generate_analysis_answer("answer", has_data=True, has_plan=True) is False
