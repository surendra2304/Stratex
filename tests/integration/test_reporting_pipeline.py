import os
import json
import pytest
from reporting.daily_report import DailyReportGenerator

def test_reporting_pipeline_execution(tmp_path):
    gen = DailyReportGenerator(reports_dir=str(tmp_path))
    res = gen.generate_daily_report(date_str='2026-08-28')
    assert res['report_date'] == '2026-08-28'
    assert 'voice_summary' in res
    assert 'financial_metrics' in res
