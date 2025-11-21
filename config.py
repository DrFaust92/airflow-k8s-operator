import os
import airflow_client.client as client

AIRFLOW_HOST = os.getenv("AIRFLOW_HOST")  # get from env var
if not AIRFLOW_HOST:
	raise RuntimeError("Environment variable AIRFLOW_HOST must be set")

# obtain an access token (implement generate_access_token elsewhere)
access_token = generate_access_token("admin", "admin", AIRFLOW_HOST)  # generate from gcp for composer instead

configuration = client.Configuration(host=AIRFLOW_HOST, access_token=access_token)
api_client = client.ApiClient(configuration=configuration)