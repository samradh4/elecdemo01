from fastapi.testclient import TestClient
from main import app

c=TestClient(app)
assert c.get('/health').status_code==200
v={"epicId":"ABC1234567","name":"Test Voter","age":30,"gender":"Male","serialNo":"1","partNo":"10","boothNo":"2","dataQuality":"Verified"}
r=c.post('/voters',json=v)
assert r.status_code in (200,409),r.text
r=c.get('/voters',params={'query':'ABC1234567'})
assert r.status_code==200 and r.json()['total']>=1
print('backend self-test passed')
