import requests
import time
import json
import sys

BASE_URL = "https://sales-research-application-297743845367.europe-west1.run.app"
INITIATE_URL = f"{BASE_URL}/api/v1/research/initiate"
STATUS_URL_TEMPLATE = f"{BASE_URL}/api/v1/research/status/{{job_id}}"

PAYLOAD = {
    "account_id": "001123456789012",
    "company_name": "JPMorgan",
    "user_id": "005123456789012"
}

def test_live_api():
    print(f"--- Testing Live API Endpoint: {INITIATE_URL} ---")
    
    # 1. Initiate Research
    try:
        response = requests.post(INITIATE_URL, json=PAYLOAD, timeout=30)
        print(f"Initiate Response Code: {response.status_code}")
        
        if response.status_code == 401 or response.status_code == 403:
            print("[ERROR] Authentication required. Please provide an IAP JWT if AUTH_ENABLED is True.")
            return
            
        if response.status_code != 202:
            print(f"[ERROR] Failed to initiate: {response.text}")
            return
            
        data = response.json()
        job_id = data.get("job_id")
        print(f"Successfully initiated! Job ID: {job_id}")
        
    except Exception as e:
        print(f"[ERROR] Connection failed: {e}")
        return

    # 2. Poll Status
    print(f"\n--- Polling Status for Job: {job_id} ---")
    max_retries = 20
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            status_url = STATUS_URL_TEMPLATE.format(job_id=job_id)
            status_response = requests.get(status_url, timeout=30)
            
            if status_response.status_code == 200:
                status_data = status_response.json()
                status = status_data.get("status")
                progress = status_data.get("progress")
                step = status_data.get("current_step")
                
                print(f"[{time.strftime('%H:%M:%S')}] Status: {status} | Progress: {progress}% | Step: {step}")
                
                if status == "COMPLETED":
                    print("\n[SUCCESS] Research job completed successfully!")
                    break
                if status == "FAILED":
                    print(f"\n[FAILURE] Research job failed: {status_data}")
                    break
            else:
                print(f"[WARNING] Status check returned {status_response.status_code}: {status_response.text}")
                
        except Exception as e:
            print(f"[WARNING] Status poll error: {e}")
            
        time.sleep(15) # Wait between polls
        retry_count += 1
        
    if retry_count >= max_retries:
        print("\n[TIMEOUT] Reached max polling retries. The job might still be running.")

if __name__ == "__main__":
    test_live_api()
