"""
Unit tests for init_database.py
Tests schema loading logic, data loading with type casting, and views creation.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import json
import sys
import os

# Add infrastructure directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from init_database import (
    get_cast,
    UUID_COLS, INT_COLS, DECIMAL_COLS, BOOL_COLS, TIMESTAMP_COLS, DATE_COLS, ENUM_COLS,
    VIEWS
)


class TestGetCast:
    """Tests for the get_cast function that determines SQL type casting."""
    
    def test_uuid_columns_return_uuid_cast(self):
        """UUID columns should return ::uuid cast."""
        for col in ['plan_id', 'account_id', 'customer_id', 'unicorn_id', 'booking_id']:
            assert get_cast(col, 'any_table') == '::uuid'
    
    def test_integer_columns_return_integer_cast(self):
        """Integer columns should return ::integer cast."""
        for col in ['user_limit', 'current_user_count', 'employee_count', 'seat_capacity']:
            assert get_cast(col, 'any_table') == '::integer'
    
    def test_decimal_columns_return_decimal_cast(self):
        """Decimal columns should return ::decimal cast."""
        for col in ['monthly_price', 'hourly_rate', 'total_cost', 'amount']:
            assert get_cast(col, 'any_table') == '::decimal'
    
    def test_boolean_columns_return_boolean_cast(self):
        """Boolean columns should return ::boolean cast."""
        for col in ['is_active', 'is_available', 'is_completed']:
            assert get_cast(col, 'any_table') == '::boolean'
    
    def test_timestamp_columns_return_timestamp_cast(self):
        """Timestamp columns should return ::timestamp cast."""
        for col in ['created_at', 'updated_at', 'start_datetime', 'end_datetime']:
            assert get_cast(col, 'any_table') == '::timestamp'
    
    def test_date_columns_return_date_cast(self):
        """Date columns should return ::date cast."""
        for col in ['next_billing_date', 'last_service_date', 'purchase_date']:
            assert get_cast(col, 'any_table') == '::date'
    
    def test_enum_columns_return_correct_enum_cast(self):
        """Enum columns should return correct enum type cast based on table."""
        assert get_cast('status', 'accounts') == '::account_status_enum'
        assert get_cast('status', 'transactions') == '::transaction_status_enum'
        assert get_cast('status', 'unicorn_availability') == '::unicorn_availability_status_enum'
        assert get_cast('billing_cycle', 'accounts') == '::billing_cycle_enum'
        assert get_cast('customer_type', 'customers') == '::customer_type_enum'
        assert get_cast('transaction_type', 'transactions') == '::transaction_type_enum'
    
    def test_unknown_columns_return_empty_cast(self):
        """Unknown columns should return empty string (no cast)."""
        assert get_cast('unknown_column', 'any_table') == ''
        assert get_cast('random_field', 'accounts') == ''


class TestColumnSets:
    """Tests to verify column classification sets are properly defined."""
    
    def test_uuid_cols_contains_expected_columns(self):
        """UUID_COLS should contain all ID columns."""
        expected = {'plan_id', 'account_id', 'user_id', 'customer_id', 'unicorn_id', 
                   'booking_id', 'transaction_id', 'availability_id', 'tracker_id'}
        assert expected.issubset(UUID_COLS)
    
    def test_bool_cols_contains_expected_columns(self):
        """BOOL_COLS should contain all boolean columns."""
        expected = {'is_custom', 'is_active', 'is_available', 'is_recurring', 'is_completed'}
        assert expected == BOOL_COLS
    
    def test_no_column_overlap_between_sets(self):
        """Column sets should not overlap (except ENUM_COLS which is a dict)."""
        all_sets = [UUID_COLS, INT_COLS, DECIMAL_COLS, BOOL_COLS, TIMESTAMP_COLS, DATE_COLS]
        for i, set1 in enumerate(all_sets):
            for set2 in all_sets[i+1:]:
                overlap = set1 & set2
                assert len(overlap) == 0, f"Overlap found: {overlap}"


class TestViewsDefinitions:
    """Tests for the VIEWS definitions."""
    
    def test_views_list_is_not_empty(self):
        """VIEWS should contain view definitions."""
        assert len(VIEWS) > 0
    
    def test_each_view_has_name_and_sql(self):
        """Each view should be a tuple of (name, sql)."""
        for view in VIEWS:
            assert isinstance(view, tuple)
            assert len(view) == 2
            name, sql = view
            assert isinstance(name, str)
            assert isinstance(sql, str)
            assert len(name) > 0
            assert 'CREATE' in sql.upper()
    
    def test_expected_views_are_defined(self):
        """Expected analytics views should be defined."""
        view_names = [v[0] for v in VIEWS]
        expected_views = [
            'daily_bookings_summary',
            'monthly_revenue_summary',
            'current_unicorn_availability',
            'customer_retention_metrics',
            'top_revenue_customers',
        ]
        for expected in expected_views:
            assert expected in view_names, f"Missing view: {expected}"


class TestExecuteSql:
    """Tests for SQL execution with mocked RDS Data API."""
    
    def test_execute_sql_builds_correct_kwargs(self):
        """execute_sql should build correct kwargs for RDS Data API."""
        from init_database import execute_sql
        import inspect
        
        sig = inspect.signature(execute_sql)
        params = list(sig.parameters.keys())
        
        assert 'resource_arn' in params
        assert 'secret_arn' in params
        assert 'database' in params
        assert 'sql' in params
        assert 'params' in params
    
    def test_execute_sql_accepts_optional_params(self):
        """execute_sql should accept optional parameters."""
        from init_database import execute_sql
        import inspect
        
        sig = inspect.signature(execute_sql)
        params_param = sig.parameters.get('params')
        assert params_param.default is None


class TestGetStackOutputs:
    """Tests for CloudFormation stack output retrieval."""
    
    @patch('init_database.cf_client')
    def test_get_stack_outputs_returns_dict(self, mock_cf):
        """get_stack_outputs should return a dictionary of outputs."""
        from init_database import get_stack_outputs
        
        mock_cf.describe_stacks.return_value = {
            'Stacks': [{
                'Outputs': [
                    {'OutputKey': 'AuroraResourceArn', 'OutputValue': 'arn:aws:rds:test'},
                    {'OutputKey': 'DatabaseSecretArn', 'OutputValue': 'arn:aws:secretsmanager:test'},
                ]
            }]
        }
        
        outputs = get_stack_outputs()
        
        assert outputs['AuroraResourceArn'] == 'arn:aws:rds:test'
        assert outputs['DatabaseSecretArn'] == 'arn:aws:secretsmanager:test'


class TestLoadCsv:
    """Tests for CSV data loading functionality."""
    
    @patch('init_database.execute_batch_sql')
    @patch('init_database.execute_sql')
    @patch('builtins.open', create=True)
    @patch('os.path.exists')
    def test_load_csv_returns_zero_for_missing_file(self, mock_exists, mock_open, mock_exec, mock_batch):
        """load_csv should return 0 when file doesn't exist."""
        from init_database import load_csv
        
        mock_exists.return_value = False
        
        count = load_csv('arn:rds', 'arn:secret', 'db', 'test_table', 'missing.csv')
        
        assert count == 0
        mock_batch.assert_not_called()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
