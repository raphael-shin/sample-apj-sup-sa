"""
Unit tests for generate_embeddings.py
Tests embedding generation, vector storage, and semantic search.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import json
import sys
import os

# Add infrastructure directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_embeddings import (
    generate_table_text,
    generate_column_text,
    EMBEDDING_DIMENSION
)


class TestGenerateTableText:
    """Tests for the generate_table_text function."""
    
    def test_generates_text_with_table_name(self):
        """generate_table_text should include table name."""
        table = {
            'name': 'customers',
            'description': 'Customer records',
            'columns': [{'name': 'id', 'type': 'string'}],
            'foreign_keys': []
        }
        result = generate_table_text(table)
        assert 'customers' in result
        assert 'Table:' in result
    
    def test_generates_text_with_description(self):
        """generate_table_text should include description."""
        table = {
            'name': 'unicorns',
            'description': 'Unicorn fleet inventory',
            'columns': [{'name': 'id', 'type': 'string'}],
            'foreign_keys': []
        }
        result = generate_table_text(table)
        assert 'Unicorn fleet inventory' in result
    
    def test_generates_text_with_columns(self):
        """generate_table_text should include column information."""
        table = {
            'name': 'bookings',
            'description': 'Booking records',
            'columns': [
                {'name': 'booking_id', 'type': 'string'},
                {'name': 'customer_id', 'type': 'string'},
                {'name': 'total_cost', 'type': 'decimal'}
            ],
            'foreign_keys': []
        }
        result = generate_table_text(table)
        assert 'booking_id' in result
        assert 'customer_id' in result
        assert 'Columns:' in result
    
    def test_generates_text_with_foreign_keys(self):
        """generate_table_text should include related tables."""
        table = {
            'name': 'bookings',
            'description': 'Booking records',
            'columns': [{'name': 'id', 'type': 'string'}],
            'foreign_keys': [
                {'column': 'customer_id', 'references': {'table': 'customers', 'column': 'customer_id'}},
                {'column': 'unicorn_id', 'references': {'table': 'unicorns', 'column': 'unicorn_id'}}
            ]
        }
        result = generate_table_text(table)
        assert 'Related to:' in result
        assert 'customers' in result
        assert 'unicorns' in result
    
    def test_limits_columns_to_ten(self):
        """generate_table_text should limit columns to first 10."""
        table = {
            'name': 'large_table',
            'description': 'Table with many columns',
            'columns': [{'name': f'col_{i}', 'type': 'string'} for i in range(15)],
            'foreign_keys': []
        }
        result = generate_table_text(table)
        assert 'col_0' in result
        assert 'col_9' in result


class TestGenerateColumnText:
    """Tests for the generate_column_text function."""
    
    def test_generates_text_with_column_name(self):
        """generate_column_text should include column name."""
        column = {'name': 'customer_id', 'type': 'string', 'comment': 'Primary key'}
        result = generate_column_text('customers', column)
        assert 'customer_id' in result
        assert 'Column' in result
    
    def test_generates_text_with_table_context(self):
        """generate_column_text should include table name for context."""
        column = {'name': 'email', 'type': 'string', 'comment': 'Email address'}
        result = generate_column_text('customers', column)
        assert 'customers' in result
        assert 'in table' in result
    
    def test_generates_text_with_type(self):
        """generate_column_text should include column type."""
        column = {'name': 'hourly_rate', 'type': 'decimal(10,2)', 'comment': 'Rental rate'}
        result = generate_column_text('unicorns', column)
        assert 'decimal(10,2)' in result
        assert 'Type:' in result
    
    def test_generates_text_with_comment(self):
        """generate_column_text should include comment."""
        column = {'name': 'magic_abilities', 'type': 'string', 'comment': 'Description of magical powers'}
        result = generate_column_text('unicorns', column)
        assert 'Description of magical powers' in result
    
    def test_generates_text_with_enum_values(self):
        """generate_column_text should include enum values when present."""
        column = {
            'name': 'status', 'type': 'string', 'comment': 'Account status',
            'enum_values': ['active', 'suspended', 'terminated']
        }
        result = generate_column_text('accounts', column)
        assert 'Valid values:' in result
        assert 'active' in result
        assert 'suspended' in result
    
    def test_handles_missing_comment(self):
        """generate_column_text should handle missing comment gracefully."""
        column = {'name': 'some_field', 'type': 'string'}
        result = generate_column_text('test_table', column)
        assert 'some_field' in result


class TestEmbeddingDimension:
    """Tests for embedding configuration."""
    
    def test_embedding_dimension_is_correct(self):
        """EMBEDDING_DIMENSION should match Titan v2 output."""
        assert EMBEDDING_DIMENSION == 1024


class TestGenerateEmbedding:
    """Tests for the generate_embedding function."""
    
    @patch('generate_embeddings.bedrock_client')
    def test_generate_embedding_calls_bedrock(self, mock_bedrock):
        """generate_embedding should call Bedrock with correct parameters."""
        from generate_embeddings import generate_embedding
        
        mock_response = MagicMock()
        mock_response.__getitem__ = Mock(return_value=MagicMock())
        mock_response['body'].read.return_value = json.dumps({'embedding': [0.1] * 1024})
        mock_bedrock.invoke_model.return_value = mock_response
        
        result = generate_embedding('test text')
        
        mock_bedrock.invoke_model.assert_called_once()
        call_kwargs = mock_bedrock.invoke_model.call_args[1]
        assert call_kwargs['modelId'] == 'amazon.titan-embed-text-v2:0'
    
    @patch('generate_embeddings.bedrock_client')
    def test_generate_embedding_returns_vector(self, mock_bedrock):
        """generate_embedding should return embedding vector."""
        from generate_embeddings import generate_embedding
        
        expected_embedding = [0.1, 0.2, 0.3] + [0.0] * 1021
        mock_response = MagicMock()
        mock_response['body'].read.return_value = json.dumps({'embedding': expected_embedding})
        mock_bedrock.invoke_model.return_value = mock_response
        
        result = generate_embedding('test text')
        assert result == expected_embedding
        assert len(result) == 1024
    
    @patch('generate_embeddings.time.sleep')
    @patch('generate_embeddings.bedrock_client')
    def test_generate_embedding_retries_on_failure(self, mock_bedrock, mock_sleep):
        """generate_embedding should retry on transient failures."""
        from generate_embeddings import generate_embedding
        
        mock_response = MagicMock()
        mock_response['body'].read.return_value = json.dumps({'embedding': [0.1] * 1024})
        mock_bedrock.invoke_model.side_effect = [Exception("Rate limited"), mock_response]
        
        result = generate_embedding('test text')
        assert mock_bedrock.invoke_model.call_count == 2
        assert len(result) == 1024


class TestStoreEmbedding:
    """Tests for the store_embedding function."""
    
    @patch('generate_embeddings.execute_sql')
    def test_store_embedding_inserts_record(self, mock_exec):
        """store_embedding should insert embedding into database."""
        from generate_embeddings import store_embedding
        
        store_embedding(
            'arn:rds:cluster', 'arn:secret', 'test_db',
            entity_type='table', entity_name='customers', parent_entity=None,
            description='Customer records', metadata={'columns': ['id', 'name']},
            embedding=[0.1] * 1024
        )
        
        mock_exec.assert_called_once()
        sql = mock_exec.call_args[0][3]
        assert 'INSERT INTO metadata_embeddings' in sql
    
    @patch('generate_embeddings.execute_sql')
    def test_store_embedding_includes_chunks(self, mock_exec):
        """store_embedding should include searchable chunks text."""
        from generate_embeddings import store_embedding
        
        store_embedding(
            'arn:rds:cluster', 'arn:secret', 'test_db',
            entity_type='column', entity_name='email', parent_entity='customers',
            description='Email address', metadata={}, embedding=[0.1] * 1024
        )
        
        params = mock_exec.call_args[0][4]
        chunks_param = next(p for p in params if p['name'] == 'chunks')
        chunks_value = chunks_param['value']['stringValue']
        assert 'column' in chunks_value
        assert 'email' in chunks_value
        assert 'customers' in chunks_value


class TestSemanticSearch:
    """Tests for the semantic_search function."""
    
    @patch('generate_embeddings.generate_embedding')
    @patch('generate_embeddings.execute_sql')
    def test_semantic_search_returns_results(self, mock_exec, mock_embed):
        """semantic_search should return matching results."""
        from generate_embeddings import semantic_search
        
        mock_embed.return_value = [0.1] * 1024
        mock_exec.return_value = {
            'records': [[
                {'stringValue': 'uuid-1'},
                {'stringValue': 'table: customers. Customer records'},
                {'stringValue': '{"entity_type": "table", "entity_name": "customers"}'},
                {'stringValue': '{}'},
                {'doubleValue': 0.95}
            ]]
        }
        
        results = semantic_search('arn:rds', 'arn:secret', 'db', 'customer info')
        assert len(results) == 1
        assert results[0]['entity_name'] == 'customers'
        assert results[0]['similarity'] == 0.95
    
    @patch('generate_embeddings.generate_embedding')
    @patch('generate_embeddings.execute_sql')
    def test_semantic_search_generates_query_embedding(self, mock_exec, mock_embed):
        """semantic_search should generate embedding for query."""
        from generate_embeddings import semantic_search
        
        mock_embed.return_value = [0.1] * 1024
        mock_exec.return_value = {'records': []}
        
        semantic_search('arn:rds', 'arn:secret', 'db', 'revenue data')
        mock_embed.assert_called_once_with('revenue data')
    
    @patch('generate_embeddings.generate_embedding')
    @patch('generate_embeddings.execute_sql')
    def test_semantic_search_respects_top_k(self, mock_exec, mock_embed):
        """semantic_search should limit results to top_k."""
        from generate_embeddings import semantic_search
        
        mock_embed.return_value = [0.1] * 1024
        mock_exec.return_value = {'records': []}
        
        semantic_search('arn:rds', 'arn:secret', 'db', 'test', top_k=5)
        
        params = mock_exec.call_args[0][4]
        top_k_param = next(p for p in params if p['name'] == 'top_k')
        assert top_k_param['value']['longValue'] == 5


class TestCreateEmbeddingsTable:
    """Tests for the create_embeddings_table function."""
    
    @patch('generate_embeddings.execute_sql')
    def test_creates_table_with_correct_schema(self, mock_exec):
        """create_embeddings_table should create Bedrock KB compatible schema."""
        from generate_embeddings import create_embeddings_table
        
        create_embeddings_table('arn:rds', 'arn:secret', 'test_db')
        assert mock_exec.call_count >= 2
        
        create_call = None
        for call in mock_exec.call_args_list:
            sql = call[0][3]
            if 'CREATE TABLE metadata_embeddings' in sql:
                create_call = sql
                break
        
        assert create_call is not None
        assert 'chunks TEXT' in create_call
        assert 'embedding vector' in create_call
        assert 'metadata JSON' in create_call


class TestLoadMetadata:
    """Tests for the load_metadata function."""
    
    @patch('builtins.open', create=True)
    def test_load_metadata_reads_json(self, mock_open):
        """load_metadata should read and parse JSON file."""
        from generate_embeddings import load_metadata
        
        test_metadata = {'database': 'timely_unicorn', 'tables': [{'name': 'customers', 'columns': []}]}
        
        with patch('json.load', return_value=test_metadata):
            result = load_metadata()
        
        assert result['database'] == 'timely_unicorn'
        assert len(result['tables']) == 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
