import os
import airflow_client.client as client
from airflow_client.client.api.variable_api import VariableApi
from airflow_client.client.model.variable import Variable


# This is a sanity test to see that everything is wired up correctly.
def test_create_variable():
    # Set up configuration
    airflow_host = os.getenv("AIRFLOW_HOST", "http://localhost:8080")
    airflow_username = os.getenv("AIRFLOW_USERNAME", "admin")
    airflow_password = os.getenv("AIRFLOW_PASSWORD", "admin")

    # Ensure AIRFLOW_HOST includes /api/v1
    if not airflow_host.endswith("/api/v1"):
        if airflow_host.endswith("/"):
            airflow_host = airflow_host + "api/v1"
        else:
            airflow_host = airflow_host + "/api/v1"

    configuration = client.Configuration(
        host=airflow_host, username=airflow_username, password=airflow_password
    )

    # Create API client
    api_client = client.ApiClient(configuration=configuration)

    # Create Variable API instance
    variable_api = VariableApi(api_client)

    # Create a test variable
    test_variable = Variable(key="test_variable", value="test_value")

    try:
        # Create the variable
        response = variable_api.post_variables(test_variable)
        print(f"Successfully created variable: {response}")

        # Optionally, verify by getting the variable
        retrieved_variable = variable_api.get_variable("test_variable")
        print(f"Retrieved variable: {retrieved_variable}")

        # Clean up: delete the test variable
        variable_api.delete_variable("test_variable")
        print("Test variable deleted successfully")

    except Exception as e:
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    test_create_variable()
