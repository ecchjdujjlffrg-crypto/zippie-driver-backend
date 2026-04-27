import asyncio
import datetime
import json
import hashlib
import requests
from aiohttp import web, WSMsgType

active_orders = {}
completed_orders = {}
driver_sessions = {}
drivers = {}
driver_stats = {}
driver_tokens = {}  # {login: fcm_token}

# Firebase Server Key
FCM_SERVER_KEY = "BHdFs6ZfDwSN-kqhQqs9SV55Bs3KT9--Gy7CNVXwMMrUGUFWI397NOn3hz1-rs5OuhjcrDQCeWKc_cjQRLLnURM"

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ==================== FIREBASE PUSH ====================

def send_fcm_push(token, title, body):
    """Отправка push-уведомления через Firebase"""
    if not token:
        return
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'key={FCM_SERVER_KEY}'
    }
    
    payload = {
        'to': token,
        'notification': {
            'title': title,
            'body': body,
            'sound': 'default',
            'vibrate': 'true'
        }
    }
    
    try:
        response = requests.post('https://fcm.googleapis.com/fcm/send', 
                                 headers=headers, json=payload, timeout=5)
        print(f"📤 FCM отправлено: {response.status_code}")
    except Exception as e:
        print(f"❌ FCM ошибка: {e}")

def send_push_to_all_drivers(title, body):
    """Отправка push всем водителям"""
    for login, token in driver_tokens.items():
        send_fcm_push(token, title, body)

# ==================== ОБРАБОТЧИКИ ====================

async def handle_site(request):
    with open('index.html', 'r', encoding='utf-8') as f:
        return web.Response(text=f.read(), content_type='text/html')

async def handle_admin(request):
    with open('admin.html', 'r', encoding='utf-8') as f:
        return web.Response(text=f.read(), content_type='text/html')

async def handle_register(request):
    try:
        data = await request.json()
        login = data.get('login')
        raw_password = data.get('password')
        if login in drivers:
            return web.json_response({"success": False, "message": "Логин уже существует"})
        drivers[login] = {
            "password": hash_password(raw_password),
            "raw_password": raw_password,
            "full_name": data.get('full_name'),
            "phone": data.get('phone'),
            "car_model": data.get('car_model', ''),
            "car_year": data.get('car_year', ''),
            "car_color": data.get('car_color', ''),
            "car_plate": data.get('car_plate', '')
        }
        with open('drivers.json', 'w', encoding='utf-8') as f:
            json.dump(drivers, f, ensure_ascii=False, indent=2)
        return web.json_response({"success": True, "message": f"Водитель {data.get('full_name')} создан"})
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)})

async def handle_login(request):
    try:
        data = await request.json()
        login = data.get('login')
        password = data.get('password')
        fcm_token = data.get('fcm_token')
        
        if login in drivers and drivers[login]["password"] == hash_password(password):
            if fcm_token:
                driver_tokens[login] = fcm_token
                print(f"📱 Токен сохранён для {login}")
            return web.json_response({
                "success": True,
                "full_name": drivers[login]["full_name"],
                "phone": drivers[login]["phone"],
                "car_model": drivers[login].get("car_model", ""),
                "car_year": drivers[login].get("car_year", ""),
                "car_color": drivers[login].get("car_color", ""),
                "car_plate": drivers[login].get("car_plate", "")
            })
        return web.json_response({"success": False, "message": "Неверный логин или пароль"})
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)})

async def handle_update_fcm_token(request):
    """Обновление FCM токена водителя"""
    try:
        data = await request.json()
        login = data.get('login')
        token = data.get('fcm_token')
        if token and login:
            driver_tokens[login] = token
            print(f"📱 Обновлён токен для {login}")
        return web.json_response({"success": True})
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)})

async def handle_drivers(request):
    drivers_list = [{
        "login": k,
        "full_name": v["full_name"],
        "phone": v["phone"],
        "raw_password": v.get("raw_password", ""),
        "car_model": v.get("car_model", ""),
        "car_year": v.get("car_year", ""),
        "car_color": v.get("car_color", ""),
        "car_plate": v.get("car_plate", "")
    } for k, v in drivers.items()]
    return web.json_response(drivers_list)

async def handle_delete_driver(request):
    try:
        data = await request.json()
        login = data.get('login')
        if login not in drivers:
            return web.json_response({"success": False, "message": "Водитель не найден"})
        
        # Удаляем токен
        if login in driver_tokens:
            del driver_tokens[login]
        
        del drivers[login]
        
        with open('drivers.json', 'w', encoding='utf-8') as f:
            json.dump(drivers, f, ensure_ascii=False, indent=2)
        
        return web.json_response({"success": True, "message": f"Водитель {login} удалён"})
    except Exception as e:
        return web.json_response({"success": False, "message": str(e)})

async def handle_new_order(request):
    # Обработка предварительного запроса OPTIONS (CORS)
    if request.method == 'OPTIONS':
        return web.Response(
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'POST, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type'
            }
        )
    try:
        data = await request.json()
        order_id = str(int(datetime.datetime.now().timestamp() * 1000))
        
        # Поддержка нескольких адресов
        addresses = []
        if data.get('addresses'):
            addresses = data.get('addresses')
        elif data.get('addr_a'):
            addresses.append(data.get('addr_a'))
            if data.get('addr_b'):
                addresses.append(data.get('addr_b'))
        
        data['addresses'] = addresses
        data['addr_a'] = addresses[0] if len(addresses) > 0 else ''
        data['addr_b'] = addresses[-1] if len(addresses) > 1 else ''
        
        active_orders[order_id] = {
            'order_id': order_id,
            'order_data': data,
            'is_active': 0,
            'is_finished': 0,
            'driver_name': None,
            'created_at': datetime.datetime.now().isoformat()
        }
        print(f"📦 Новый заказ: {order_id} | Адресов: {len(addresses)}")
        
        # Отправляем WebSocket уведомление всем водителям
        await broadcast_orders()
        
        # Отправляем Firebase Push уведомления всем водителям
        service_name = data.get('service', 'ЗАКАЗ')
        addr_start = addresses[0] if addresses else ''
        addr_end = addresses[-1] if len(addresses) > 1 else ''
        title = f"🆕 {service_name}"
        body = f"{addr_start} → {addr_end}"
        send_push_to_all_drivers(title, body)
        
        return web.Response(
            text="OK",
            headers={'Access-Control-Allow-Origin': '*'}
        )
    except Exception as e:
        print(e)
        return web.Response(status=500, headers={'Access-Control-Allow-Origin': '*'})

async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    driver_name = request.query.get('name', '')
    
    # Проверяем, существует ли водитель
    if driver_name not in drivers:
        print(f"❌ Водитель {driver_name} не найден")
        await ws.send_json({'type': 'error', 'message': 'Водитель не найден'})
        await ws.close()
        return ws
    
    if driver_name:
        driver_sessions[driver_name] = ws
        print(f"🔌 Водитель {driver_name} подключился")
    
    active = {k: v for k, v in active_orders.items() if v.get('is_finished') == 0}
    await ws.send_json({'type': 'orders', 'orders': active})
    
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
                action = data.get('action')
                order_id = data.get('order_id')
                print(f"📨 Получено: {action} для заказа {order_id} от {driver_name}")
                
                if action == 'take' and order_id in active_orders:
                    order = active_orders[order_id]
                    print(f"DEBUG take: is_active={order.get('is_active')}, is_finished={order.get('is_finished')}, current_driver={order.get('driver_name')}")
                    
                    if order.get('is_active') == 1:
                        await ws.send_json({'type': 'take_rejected', 'order_id': order_id, 'message': 'Заказ уже принят другим водителем'})
                        print(f"❌ Водитель {driver_name} пытался принять уже занятый заказ {order_id}")
                        continue
                    elif order.get('is_finished') == 1:
                        await ws.send_json({'type': 'take_rejected', 'order_id': order_id, 'message': 'Заказ уже завершён'})
                        print(f"❌ Водитель {driver_name} пытался принять завершённый заказ {order_id}")
                        continue
                    
                    if order.get('is_active') == 0 and order.get('is_finished') == 0:
                        order['is_active'] = 1
                        order['driver_name'] = driver_name
                        print(f"✅ Заказ {order_id} принят водителем {driver_name}")
                        await broadcast_orders()
                    else:
                        print(f"❌ Не удалось принять заказ {order_id}")
                        
                elif action == 'reject' and order_id in active_orders:
                    order = active_orders[order_id]
                    if order.get('driver_name') == driver_name:
                        order['is_active'] = 0
                        order['driver_name'] = None
                        print(f"❌ Заказ {order_id} отклонён водителем {driver_name}")
                        await broadcast_orders()
                        
                elif action == 'finish' and order_id in active_orders:
                    order = active_orders[order_id]
                    print(f"DEBUG finish: driver_name={order.get('driver_name')}, current={driver_name}, is_finished={order.get('is_finished')}")
                    if order.get('driver_name') == driver_name and order.get('is_finished') == 0:
                        order['is_finished'] = 1
                        order['completed_at'] = datetime.datetime.now().isoformat()
                        completed_orders[order_id] = order
                        del active_orders[order_id]
                        price = int(order.get('order_data', {}).get('price', 0))
                        if driver_name not in driver_stats:
                            driver_stats[driver_name] = {'completed': 0, 'earned': 0}
                        driver_stats[driver_name]['completed'] += 1
                        driver_stats[driver_name]['earned'] += price
                        print(f"🏁 Заказ {order_id} завершён водителем {driver_name}")
                        print(f"📊 Активных заказов после удаления: {len(active_orders)}")
                        await broadcast_orders()
                    else:
                        print(f"❌ Не удалось завершить заказ {order_id}")
            except Exception as e:
                print(f"Ошибка: {e}")
    
    if driver_name in driver_sessions:
        del driver_sessions[driver_name]
        print(f"🔌 Водитель {driver_name} отключился")
    return ws

async def broadcast_orders():
    active = {k: v for k, v in active_orders.items() if v.get('is_finished') == 0}
    for ws in driver_sessions.values():
        try:
            await ws.send_json({'type': 'orders', 'orders': active})
        except:
            pass

def load_drivers():
    global drivers
    try:
        with open('drivers.json', 'r', encoding='utf-8') as f:
            drivers = json.load(f)
        print(f"📂 Загружено {len(drivers)} водителей")
    except FileNotFoundError:
        drivers = {}
        print("📂 Файл drivers.json не найден, создан новый")

# ==================== ЗАПУСК ====================

async def main():
    load_drivers()
    
    app = web.Application()
    app.router.add_get('/', handle_site)
    app.router.add_get('/admin', handle_admin)
    app.router.add_post('/register', handle_register)
    app.router.add_post('/login', handle_login)
    app.router.add_post('/update_fcm_token', handle_update_fcm_token)
    app.router.add_get('/drivers', handle_drivers)
    app.router.add_post('/delete_driver', handle_delete_driver)
    app.router.add_post('/new_order', handle_new_order)
    app.router.add_options('/new_order', handle_new_order)
    app.router.add_get('/ws', websocket_handler)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    
    print("="*50)
    print("✅ СЕРВЕР ЗАПУЩЕН!")
    print("="*50)
    print("📱 САЙТ ЗАКАЗЧИКА: http://localhost:8080/")
    print("👨‍💼 АДМИН-ПАНЕЛЬ: http://localhost:8080/admin")
    print("🚚 WebSocket: ws://localhost:8080/ws")
    print("📱 Firebase Push: активен")
    print("="*50)
    
    await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())