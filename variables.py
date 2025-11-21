import kopf
import airflow_client.client as client
from airflow_client.client.model.variable import Variable
from config import api_client

variables_api = client.apis.VariableApi(api_client=api_client)
@kopf.on.create('airflow.drfaust92', 'v1beta1', 'variables')
def create_variable(meta, spec, namespace, logger, body, **kwargs):
    var_name = spec.get('name')
    var_value = spec.get('value')
    var_description = spec.get('description', '')

    logger.info(f"Creating Airflow Variable: {var_name} with value: [MASKED] and description: {var_description}")
    try:
        variable = Variable(
            key=var_name,
            value=var_value,
            description=var_description
        )
        variables_api.post_variables(variable)
        return {'message': f'Variable {var_name} created successfully.'}
    except Exception as e:
        logger.error(f"Failed to create Airflow Variable {var_name}: {e}")
        return {'error': f'Failed to create variable {var_name}: {e}'}
    
@kopf.on.delete('airflow.drfaust92', 'v1beta1', 'variables')
def delete_variable(meta, spec, namespace, logger, body, **kwargs):
    var_name = spec.get('name')

    logger.info(f"Deleting Airflow Variable: {var_name}")
    try:
        variables_api.delete_variable(
            variable_key=var_name
            )
        return {'message': f'Variable {var_name} deleted successfully.'}
    except Exception as e:
        logger.error(f"Failed to delete Airflow Variable {var_name}: {e}")
        return {'error': f'Failed to delete variable {var_name}: {e}'}