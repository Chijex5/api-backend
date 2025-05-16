import eventlet
from dotenv import load_dotenv
eventlet.monkey_patch()
load_dotenv()
from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
import json
import os
from flask_cors import CORS
from flask_mysqldb import MySQL
import uuid
from google import genai
import re
import html

eventlet.monkey_patch()
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

CORS(app)
app.config['MYSQL_HOST'] = os.getenv('MYSQL_HOST')
app.config['MYSQL_USER'] = os.getenv('MYSQL_USER')
app.config['MYSQL_PASSWORD'] = os.getenv('MYSQL_PASSWORD')
app.config['MYSQL_DB'] = os.getenv('MYSQL_DB')
app.config['MYSQL_PORT'] = int(os.getenv('MYSQL_PORT', 3306))
mysql = MySQL(app)

# Chat States
CHAT_STATES = {
    'WAITING': 'waiting',
    'ASSIGNED': 'assigned',
    'RESOLVED': 'resolved'
}

# WebSocket Events
WS_EVENTS = {
    'NEW_ESCALATION': 'new_escalation',
    'AGENT_AVAILABLE': 'agent_available',
    'CHAT_ASSIGNED': 'chat_assigned',
    'NEW_MESSAGE': 'new_message',
    'CHAT_RESOLVED': 'chat_resolved',
    'CHAT_ESCALATED': 'chat_escalated',  # Added for frontend compatibility
    'ESCALATE_REQUEST': 'escalate_request'  # Added for frontend compatibility
}

# 🔑 Configure your Gemini API key
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

def get_db():
    return mysql.connection.cursor()

# 📦 Utility to load JSON files from /mock folder
def load_data(filename):
    base_path = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(base_path, 'mock', filename), 'r') as f:
        return json.load(f)

# Load all mock data into memory
customers = load_data('customers.json')
products = load_data('products.json')
orders = load_data('orders.json')
payments = load_data('payments.json')
policy = load_data('support_policy.json')

# 🧠 Step 1: Interpret Message and Find Customer by Email or Phone
def find_customer(identifier):
    for customer in customers:
        if customer['email'] == identifier or customer['phone'] == identifier:
            return customer
    return None

# 🧠 Step 2: Find Relevant Orders for a Customer
def find_orders_by_userid(user_id):
    return [order for order in orders if order['userid'] == user_id]

# 🧠 Step 3: Refund Eligibility Checker
def can_refund(order):
    if not order['delivered_on']:
        return False
    delivered_date = datetime.strptime(order['delivered_on'], "%Y-%m-%d")
    days_since_delivery = (datetime.now() - delivered_date).days
    return days_since_delivery <= policy['refund_window_days']

# 🧠 Step 4: Detect Simple Greetings
def is_greeting(message):
    greetings = [
        r'\bhello\b', r'\bhi\b', r'\bhey\b', 
        r'\bgood morning\b', r'\bgood afternoon\b', r'\bgood evening\b',
        r'\bhiya\b', r'\bgreetings\b'
    ]
    message = message.lower().strip()
    if len(message) > 15:
        return False
    return any(re.search(pattern, message) for pattern in greetings)

def needs_escalation_or_clarification(message, customer, order=None):
    message = message.lower().strip()
    
    # Check for incomplete information (e.g., vague or missing details)
    vague_phrases = ['help', 'issue', 'problem', 'something wrong', 'not working', 'trouble with', 'difficulties']
    if any(phrase in message for phrase in vague_phrases) and len(message.split()) < 10:
        return {'needs_clarification': True, 'is_escalating': False}
    
    # EXPANDED: Check for complex queries that exceed AI capability
    legal_terms = ['legal', 'lawsuit', 'sue', 'lawyer', 'attorney', 'court', 'litigation', 'settlement',
                  'compensation', 'legal action', 'legal representation', 'class action']
    
    security_issues = ['account hacked', 'fraud', 'stolen', 'identity theft', 'unauthorized access',
                      'compromised account', 'security breach', 'suspicious activity']
    
    special_orders = ['bulk order', 'custom order', 'wholesale', 'large quantity', 'corporate order',
                     'business account', 'special pricing', 'volume discount', 'bulk purchase']
    
    technical_issues = ['website down', 'system error', 'checkout broken', 'payment failed repeatedly',
                       'can\'t access account', 'persistent error']
    
    sensitive_issues = ['discrimination', 'harassment', 'employee complaint', 'staff behavior',
                       'privacy violation', 'data breach']
    
    # Check across all categories
    complex_categories = [legal_terms, security_issues, special_orders, technical_issues, sensitive_issues]
    for category in complex_categories:
        if any(term in message for term in category):
            return {'needs_clarification': False, 'is_escalating': True}
    
    # ADDED: Pattern-based escalation for monetary threshold
    # Escalate high-value refunds or disputes
    money_patterns = [
        r'\$\d{3,}',  # Dollar amounts $100+
        r'₦\d{5,}',   # Naira amounts ₦10000+
        r'\d+ thousand',
        r'\d+ items',  # Multiple items in dispute
    ]
    if any(re.search(pattern, message) for pattern in money_patterns):
        if any(word in message for word in ['refund', 'return', 'cancel', 'dispute', 'not received']):
            return {'needs_clarification': False, 'is_escalating': True}
    
    # ADDED: Complex request detection
    complex_request_indicators = [
        r'not satisfied with .* resolution',
        r'speak .* manager',
        r'supervisor',
        r'agent',
        r'escalate',
        r'complaint',
        r'dissatisfied',
        r'unacceptable',
    ]
    if any(re.search(pattern, message) for pattern in complex_request_indicators):
        return {'needs_clarification': False, 'is_escalating': True}
    
    # If message is too short or lacks context
    if len(message.split()) < 3 and not is_greeting(message):
        return {'needs_clarification': True, 'is_escalating': False}
    
    # ADDED: Check for repeated contacts about the same issue
    # In a real system, you would check customer contact history
    repeated_issues = ['again', 'still not resolved', 'second time', 'already contacted',
                      'previously reported', 'still waiting', 'no response']
    if any(phrase in message for phrase in repeated_issues):
        return {'needs_clarification': False, 'is_escalating': True}
    
    return {'needs_clarification': False, 'is_escalating': False}

# 📝 Format AI Response for better display
def format_ai_response(text):
    """
    Format the raw AI response with proper formatting including:
    - Convert markdown-style emphasis to HTML tags for the frontend
    - Format paragraphs, lists, and other elements
    - Highlight important information
    """
    # Format bold text (replace **, ***, and other markdown styles)
    text = re.sub(r'\*\*\*(.*?)\*\*\*', r'<strong class="highlight">\1</strong>', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.*?)\*', r'<em>\1</em>', text)
    
    # Format lists
    lines = text.split('\n')
    formatted_lines = []
    in_list = False
    
    for line in lines:
        # List items processing
        if re.match(r'^\s*[•\-\*]\s+', line):
            if not in_list:
                formatted_lines.append('<ul class="list-disc pl-5 space-y-1 my-2">')
                in_list = True
            # Clean up and format the list item
            clean_line = re.sub(r'^\s*[•\-\*]\s+', '', line)
            formatted_lines.append(f'<li>{clean_line}</li>')
        else:
            if in_list:
                formatted_lines.append('</ul>')
                in_list = False
            
            # Handle paragraphs and other formatting
            if line.strip() == '':
                formatted_lines.append('<div class="py-1"></div>')  # Spacing
            else:
                # Check for order numbers, payment IDs, etc. to highlight
                patterns = {
                    r'\b(ord\d+)\b': r'<span class="text-blue-600 font-medium">\1</span>',
                    r'\b(pay\d+)\b': r'<span class="text-blue-600 font-medium">\1</span>',
                    r'\b(₦\d+(?:,\d+)*(?:\.\d+)?)\b': r'<span class="text-green-600 font-medium">\1</span>',
                    r'\b(\$\d+(?:,\d+)*(?:\.\d+)?)\b': r'<span class="text-green-600 font-medium">\1</span>'
                }
                
                for pattern, replacement in patterns.items():
                    line = re.sub(pattern, replacement, line)
                
                # Check if it's a header-like line (Subject:, Dear, Sincerely, etc.)
                if re.match(r'^(Subject:|Dear\b|Sincerely,|The.*Team)', line):
                    formatted_lines.append(f'<div class="font-medium">{line}</div>')
                else:
                    formatted_lines.append(f'<div>{line}</div>')
    
    # Close any open list
    if in_list:
        formatted_lines.append('</ul>')
    
    return ''.join(formatted_lines)

# 🤖 AI Layer: Compose AI Response with full customer/order context
def generate_ai_response(customer, message, order=None, payment=None, product=None):
    try:
        prompt = f"""
You are a helpful and professional customer support assistant for the ShopNex e-commerce platform.

Customer Info:
Name: {customer['name']}
Email: {customer['email']}
Phone: {customer['phone']}
Address: {customer['address']}

Support Message: {message}

Order Info:
{json.dumps(order, indent=2) if order else "No order information available"}

Payment Info:
{json.dumps(payment, indent=2) if payment else "No payment information available"}

Product Info:
{json.dumps(product, indent=2) if product else "No product information available"}

Support Policy:
{json.dumps(policy, indent=2)}

Respond kindly, clearly, and informatively to the customer's concern.
Use markdown formatting for emphasis:
- Use *** for important information that should be highlighted (like action items or critical details)
- Use ** for regular emphasis
- Use bullet points for lists of steps or recommendations

Always refer to the platform as "ShopNex".
"""

        response = client.models.generate_content(
            model="gemini-2.0-flash", contents=prompt)
        
        # Get the raw text and format it
        raw_text = response.text.strip()
        formatted_text = format_ai_response(raw_text)
        
        return {
            'raw': raw_text,
            'formatted': formatted_text
        }
    except Exception as e:
        error_message = f"Sorry, something went wrong while contacting our AI assistant. Please try again later. ({e})"
        return {
            'raw': error_message,
            'formatted': f'<div>{html.escape(error_message)}</div>'
        }


@app.route('/support', methods=['POST'])
def support():
    try:
        data = request.get_json()
        identifier = data.get('identifier')
        message    = data.get('message', '')

        customer = find_customer(identifier)
        if not customer:
            return jsonify({
                'ai_response': {
                    'raw': "Hi there! We couldn't find your account. Can you double-check your email or phone number?",
                    'formatted': "<div>Hi there! We couldn't find your account. Can you double-check your email or phone number?</div>"
                },
                'is_escalating': False
            })

        customer_orders = find_orders_by_userid(customer['user_id'])
        if not customer_orders:
            txt = f"Hey {customer['name']}, we couldn't find any orders on your account. Did you use another email or phone number?"
            return jsonify({
                'ai_response': {'raw': txt, 'formatted': f"<div>{txt}</div>"},
                'is_escalating': False
            })

        last_order = customer_orders[-1]
        payment    = next((p for p in payments if p['paymentid']==last_order['paymentid']), None)
        product    = next((p for p in products if p['product_id']==last_order['product']), None)

        if is_greeting(message):
            txt = f"Hi {customer['name']}! Thanks for reaching out. How can I assist you today?"
            return jsonify({'ai_response':{'raw':txt,'formatted':f"<div>{txt}</div>"}, 'is_escalating':False})

        esc = needs_escalation_or_clarification(message, customer, last_order)
        if esc['needs_clarification']:
            txt = f"Hi {customer['name']}, could you please provide more details about your issue?"
            return jsonify({'ai_response':{'raw':txt,'formatted':f"<div>{txt}</div>"}, 'is_escalating':False})

        if esc['is_escalating']:
            cur = get_db()
            chat_id     = str(uuid.uuid4())
            case_number = f"CASE-{datetime.now():%Y%m%d%H%M%S}"
            
            # Create new chat entry
            cur.execute(
                "INSERT INTO chats (id, customer_id, customer_name, customer_email, state, case_number, created_at, messages, issue) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (chat_id, customer['user_id'], customer['name'], identifier, CHAT_STATES['WAITING'], 
                 case_number, datetime.now(), json.dumps([{
                    'from': 'customer',
                    'text': message,
                    'timestamp': datetime.now().isoformat()
                }]), message[:100])
            )
            mysql.connection.commit()

            # Notify all connected agents about the new escalation
            socketio.emit(WS_EVENTS['NEW_ESCALATION'], {
                'chat_id': chat_id,
                'case_number': case_number,
                'customer_id': identifier,
                'customer_name': customer['name'],
                'timestamp': datetime.now().isoformat(),
                'issue': message[:100],
                'priority': 'medium'  # Default priority, could be based on customer type
            })
            
            # Also emit with the frontend-expected event name
            socketio.emit(WS_EVENTS['CHAT_ESCALATED'], {
                'id': chat_id,
                'caseNumber': case_number,
                'customerName': customer['name'],
                'customerDetails': {
                    'name': customer['name'],
                    'email': customer['email'],
                    'phone': customer['phone'],
                    'type': 'regular',  # You might want to determine this based on customer data
                    'memberSince': '2023-01-01'  # Placeholder, replace with actual data
                },
                'issue': message[:100],
                'messages': [{
                    'id': str(uuid.uuid4()),
                    'content': message,
                    'sender': 'user',
                    'timestamp': datetime.now().isoformat()
                }],
                'timestamp': datetime.now().isoformat(),
                'priority': 'medium'  # Default priority
            })

            return jsonify({
                'ai_response': {
                    'raw': "We're connecting you to an agent. Please wait...",
                    'formatted': "<div>We're connecting you to an agent. Please wait...</div>"
                },
                'is_escalating': True,
                'chat_id': chat_id,
                'case_number': case_number
            })

        # Otherwise, let the AI reply
        ai_response = generate_ai_response(customer, message, order=last_order,
                                           payment=payment, product=product)
        return jsonify({'ai_response': ai_response, 'is_escalating': False})

    except Exception as e:
        print(e)
        err = "Sorry, something went wrong while processing your request."
        return jsonify({'ai_response':{'raw':err,'formatted':f"<div>{err}</div>"}, 'is_escalating':False})

@socketio.on('connect')
def handle_connect():
    print('Client connected:', request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    print('Client disconnected:', request.sid)
    # Mark agent as offline if they disconnect
    cur = get_db()
    cur.execute(
        "UPDATE agents SET online = false WHERE id = %s",
        (request.sid,)
    )
    mysql.connection.commit()

@socketio.on('agent_login')
def handle_agent_login(data):
    agent_id = request.sid
    print(f"Agent logged in: {data['name']} ({agent_id})")
    
    # Store WebSocket connection ID with agent
    cur = get_db()
    cur.execute(
        "INSERT INTO agents (id, name, email, online, status) "
        "VALUES (%s, %s, %s, true, %s) "
        "ON DUPLICATE KEY UPDATE online = true, name = %s, email = %s, status = %s",
        (agent_id, data['name'], data['email'], 'available', 
         data['name'], data['email'], 'available')
    )
    mysql.connection.commit()
    
    # Send agent status update
    emit('agent_status', {
        'status': 'online',
        'name': data['name'],
        'email': data['email']
    })
    
    # Send list of waiting chats to the agent
    cur.execute(
        "SELECT * FROM chats WHERE state = %s ORDER BY created_at",
        (CHAT_STATES['WAITING'],)
    )
    waiting_chats = cur.fetchall()
    
    for chat in waiting_chats:
        emit(WS_EVENTS['NEW_ESCALATION'], {
            'chat_id': chat['id'],
            'case_number': chat['case_number'],
            'customer_id': chat['customer_id'],
            'customer_name': chat['customer_name'],
            'timestamp': chat['created_at'].isoformat(),
            'issue': chat['issue'] if 'issue' in chat else 'Support request',
            'priority': 'medium'  # Default priority
        })

@socketio.on('agent_available')
def handle_agent_available():
    print(f"Agent {request.sid} marked as available")
    
    # Update agent status
    cur = get_db()
    cur.execute(
        "UPDATE agents SET status = 'available' WHERE id = %s",
        (request.sid,)
    )
    mysql.connection.commit()
    
    # Find next waiting chat and assign
    cur.execute(
        "SELECT * FROM chats WHERE state = %s ORDER BY created_at LIMIT 1",
        (CHAT_STATES['WAITING'],)
    )
    chat = cur.fetchone()
    
    if chat:
        # Get messages
        messages = json.loads(chat['messages']) if chat['messages'] else []
        
        # Update chat state
        cur.execute(
            "UPDATE chats SET state = %s, agent_id = %s "
            "WHERE id = %s",
            (CHAT_STATES['ASSIGNED'], request.sid, chat['id'])
        )
        cur.execute(
            "UPDATE agents SET current_chat = %s, status = 'busy' WHERE id = %s",
            (chat['id'], request.sid)
        )
        mysql.connection.commit()
        
        # Get agent info
        cur.execute("SELECT name, email FROM agents WHERE id = %s", (request.sid,))
        agent = cur.fetchone()
        
        # Prepare chat data for frontend
        chat_data = {
            'id': chat['id'],
            'chat_id': chat['id'],
            'caseNumber': chat['case_number'],
            'customerName': chat['customer_name'],
            'customerDetails': {
                'name': chat['customer_name'],
                'email': chat['customer_email'],
                'type': 'regular',  # This should be determined from customer data
                'memberSince': '2023-01-01'  # Placeholder
            },
            'issue': chat['issue'] if 'issue' in chat else 'Support request',
            'messages': [
                {
                    'id': f"msg_{i}",
                    'content': msg['text'],
                    'sender': 'user' if msg['from'] == 'customer' else msg['from'],
                    'timestamp': msg['timestamp']
                } for i, msg in enumerate(messages)
            ],
            'timestamp': chat['created_at'].isoformat(),
            'priority': 'medium',  # Default priority
            'agent_id': request.sid,
            'agent_name': agent['name'] if agent else 'Agent'
        }
        
        # Notify the agent about the assigned chat
        emit(WS_EVENTS['CHAT_ASSIGNED'], chat_data)
        
        # Add a system message about agent assignment
        system_message = {
            'from': 'system',
            'text': f"You've been connected to {agent['name'] if agent else 'an agent'}",
            'timestamp': datetime.now().isoformat()
        }
        messages.append(system_message)
        
        cur.execute(
            "UPDATE chats SET messages = %s WHERE id = %s",
            (json.dumps(messages), chat['id'])
        )
        mysql.connection.commit()
        
        # Also emit with the expected customer-side event name
        socketio.emit(WS_EVENTS['CHAT_ASSIGNED'], {
            'chat_id': chat['id'],
            'message': f"You've been connected to {agent['name'] if agent else 'an agent'}"
        }, room=chat['customer_id'])
        
        print(f"Chat {chat['id']} assigned to agent {request.sid}")
    else:
        print("No waiting chats found")


@socketio.on('resolve_chat')
def handle_resolve_chat(data):
    print(f"Resolving chat: {data}")
    chat_id = data['chat_id']
    
    cur = get_db()
    
    # Get chat details
    cur.execute(
        "SELECT customer_id, agent_id FROM chats WHERE id = %s",
        (chat_id,)
    )
    result = cur.fetchone()
    
    if not result:
        print(f"Error: Chat {chat_id} not found")
        return
    
    customer_id = result['customer_id']
    agent_id = result['agent_id']
    
    # Update chat state
    cur.execute(
        "UPDATE chats SET state = %s, resolved_at = %s WHERE id = %s",
        (CHAT_STATES['RESOLVED'], datetime.now(), chat_id)
    )
    
    # Free up agent
    cur.execute(
        "UPDATE agents SET current_chat = NULL, status = 'available' WHERE id = %s",
        (agent_id,)
    )
    mysql.connection.commit()
    
    # Notify both parties
    resolution_message = {'message': 'This chat has been marked as resolved'}
    
    # Send to customer
    socketio.emit(WS_EVENTS['CHAT_RESOLVED'], {
        'chat_id': chat_id,
        **resolution_message
    }, room=customer_id)
    
    # Send to agent
    emit(WS_EVENTS['CHAT_RESOLVED'], {
        'chat_id': chat_id,
        **resolution_message
    })

@socketio.on('escalate_request')
def handle_escalate_request(data):
    """Handle escalation requests from the customer side"""
    print(f"Escalation request: {data}")
    
    # Data should contain chat_id, userId, userType, caseNumber, priority
    chat_id = data.get('chatId') or str(uuid.uuid4())
    user_id = data.get('userId')
    case_number = data.get('caseNumber') or f"CASE-{datetime.now():%Y%m%d%H%M%S}"
    priority = data.get('priority') or 'medium'
    
    # Get user details
    customer = None
    for c in customers:
        if c['user_id'] == user_id:
            customer = c
            break
    
    if not customer:
        print(f"Error: Customer {user_id} not found")
        return
    
    # Create chat entry if it doesn't exist
    cur = get_db()
    cur.execute("SELECT id FROM chats WHERE id = %s", (chat_id,))
    exists = cur.fetchone()
    
    if not exists:
        # Create new chat
        cur.execute(
            "INSERT INTO chats (id, customer_id, customer_name, customer_email, state, case_number, created_at, messages, issue) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (chat_id, user_id, customer['name'], customer['email'], 
             CHAT_STATES['WAITING'], case_number, datetime.now(), 
             json.dumps([]), data.get('issue', 'Support request'))
        )
        mysql.connection.commit()
    
    # Notify all agents about the escalation
    socketio.emit(WS_EVENTS['CHAT_ESCALATED'], {
        'id': chat_id,
        'chat_id': chat_id,
        'caseNumber': case_number,
        'customerName': customer['name'],
        'customerDetails': {
            'name': customer['name'],
            'email': customer['email'],
            'phone': customer.get('phone', ''),
            'type': data.get('userType', 'regular'),
            'memberSince': '2023-01-01'  # Placeholder
        },
        'issue': data.get('issue', 'Support request'),
        'messages': [],
        'timestamp': datetime.now().isoformat(),
        'priority': priority
    })
    
    # Also emit with the backend event name
    socketio.emit(WS_EVENTS['NEW_ESCALATION'], {
        'chat_id': chat_id,
        'case_number': case_number,
        'customer_id': user_id,
        'customer_name': customer['name'],
        'timestamp': datetime.now().isoformat(),
        'issue': data.get('issue', 'Support request'),
        'priority': priority
    })

# ... (keep all previous imports and initial setup)

@socketio.on('join')
def on_join(data):
    """Join a chat room"""
    chat_id = data.get('chat_id')
    if chat_id:
        join_room(chat_id)
        print(f"User {request.sid} joined chat room {chat_id}")
        # Send chat history when joining
        cur = get_db()
        cur.execute(
            "SELECT messages FROM chats WHERE id = %s",
            (chat_id,)
        )
        chat = cur.fetchone()
        if chat and chat['messages']:
            messages = json.loads(chat['messages'])
            emit('chat_history', {
                'chat_id': chat_id,
                'messages': messages
            })
    else:
        print("No chat_id provided for join event")

@socketio.on('leave')
def on_leave(data):
    """Leave a chat room"""
    chat_id = data.get('chat_id')
    if chat_id:
        leave_room(chat_id)
        print(f"User {request.sid} left chat room {chat_id}")

@socketio.on('transfer_chat')
def handle_transfer_chat(data):
    chat_id = data.get('chat_id')
    new_agent_id = data.get('agent_id')
    
    cur = get_db()
    try:
        # Get current chat and agent details
        cur.execute(
            "SELECT agent_id, customer_id FROM chats WHERE id = %s",
            (chat_id,)
        )
        chat = cur.fetchone()
        if not chat:
            emit('error', {'message': 'Chat not found'})
            return

        old_agent_id = chat['agent_id']
        customer_id = chat['customer_id']

        # Update chat assignment
        cur.execute(
            "UPDATE chats SET agent_id = %s WHERE id = %s",
            (new_agent_id, chat_id)
        )
        # Free up previous agent
        cur.execute(
            "UPDATE agents SET current_chat = NULL, status = 'available' WHERE id = %s",
            (old_agent_id,)
        )
        # Assign to new agent
        cur.execute(
            "UPDATE agents SET current_chat = %s, status = 'busy' WHERE id = %s",
            (chat_id, new_agent_id)
        )
        mysql.connection.commit()

        # Notify previous agent
        emit('chat_transferred', {
            'chat_id': chat_id,
            'message': 'Chat transferred successfully'
        }, room=old_agent_id)

        # Notify new agent
        cur.execute(
            "SELECT * FROM chats WHERE id = %s",
            (chat_id,)
        )
        chat_data = cur.fetchone()
        messages = json.loads(chat_data['messages']) if chat_data['messages'] else []
        
        formatted_chat = {
            'id': chat_id,
            'caseNumber': chat_data['case_number'],
            'customerName': chat_data['customer_name'],
            'customerDetails': {
                'name': chat_data['customer_name'],
                'email': chat_data['customer_email'],
                'type': 'regular',
                'memberSince': '2023-01-01'
            },
            'issue': chat_data['issue'],
            'messages': [{
                'id': f"msg_{i}",
                'content': msg['text'],
                'sender': 'customer' if msg['from'] == 'customer' else 'agent',
                'timestamp': msg['timestamp']
            } for i, msg in enumerate(messages)],
            'timestamp': chat_data['created_at'].isoformat(),
            'priority': 'medium'
        }
        emit(WS_EVENTS['CHAT_ASSIGNED'], formatted_chat, room=new_agent_id)

        # Notify customer
        emit('agent_transferred', {
            'chat_id': chat_id,
            'message': 'You have been transferred to a new agent'
        }, room=customer_id)

    except Exception as e:
        mysql.connection.rollback()
        emit('error', {'message': str(e)})

@socketio.on('typing')
def handle_typing(data):
    chat_id = data.get('chat_id')
    is_typing = data.get('is_typing')
    user_type = data.get('user_type')  # 'agent' or 'customer'
    
    if chat_id and user_type:
        # Broadcast to other participants
        if user_type == 'agent':
            # Notify customer
            emit('typing_indicator', {
                'chat_id': chat_id,
                'is_typing': is_typing,
                'user_type': 'agent'
            }, room=chat_id, skip_sid=request.sid)
        else:
            # Notify agent
            emit('typing_indicator', {
                'chat_id': chat_id,
                'is_typing': is_typing,
                'user_type': 'customer'
            }, room=chat_id, skip_sid=request.sid)

@socketio.on('request_chat_history')
def handle_chat_history(data):
    chat_id = data.get('chat_id')
    cur = get_db()
    cur.execute(
        "SELECT messages FROM chats WHERE id = %s",
        (chat_id,)
    )
    result = cur.fetchone()
    if result and result['messages']:
        messages = json.loads(result['messages'])
        emit('chat_history', {
            'chat_id': chat_id,
            'messages': [{
                'id': f"msg_{i}",
                'content': msg['text'],
                'sender': 'customer' if msg['from'] == 'customer' else 'agent',
                'timestamp': msg['timestamp']
            } for i, msg in enumerate(messages)]
        })
# In your SocketIO event handlers:

@socketio.on('agent_message')
def handle_agent_message(data):
    chat_id = data['chat_id']
    message = data['message']
    
    # Store message in database
    cur = get_db()
    cur.execute(
        "SELECT messages FROM chats WHERE id = %s",
        (chat_id,)
    )
    result = cur.fetchone()
    
    if not result:
        print(f"Error: Chat {chat_id} not found")
        return
    
    messages = json.loads(result['messages']) if result['messages'] else []
    
    # Add new message
    new_message = {
        'from': 'agent',
        'text': message,
        'timestamp': datetime.now().isoformat()
    }
    messages.append(new_message)
    
    cur.execute(
        "UPDATE chats SET messages = %s WHERE id = %s",
        (json.dumps(messages), chat_id)
    )
    mysql.connection.commit()
    
    # Broadcast to all in chat room
    emit(WS_EVENTS['NEW_MESSAGE'], {
        'chat_id': chat_id,
        'message': message,
        'sender': 'agent',
        'id': str(uuid.uuid4()),
        'content': message,
        'timestamp': new_message['timestamp']
    }, room=chat_id)  # Changed to chat_id room

@socketio.on('customer_message')
def handle_customer_message(data):
    chat_id = data['chat_id']
    message = data['message']
    
    # Store message in database
    cur = get_db()
    cur.execute(
        "SELECT messages, agent_id FROM chats WHERE id = %s",
        (chat_id,)
    )
    result = cur.fetchone()
    
    if not result:
        print(f"Error: Chat {chat_id} not found")
        return
    
    messages = json.loads(result['messages']) if result['messages'] else []
    
    # Add new message
    new_message = {
        'from': 'customer',
        'text': message,
        'timestamp': datetime.now().isoformat()
    }
    messages.append(new_message)
    
    cur.execute(
        "UPDATE chats SET messages = %s WHERE id = %s",
        (json.dumps(messages), chat_id)
    )
    mysql.connection.commit()
    
    # Broadcast to all in chat room
    emit(WS_EVENTS['NEW_MESSAGE'], {
        'chat_id': chat_id,
        'message': message,
        'sender': 'customer',
        'id': str(uuid.uuid4()),
        'content': message,
        'timestamp': new_message['timestamp']
    }, room=chat_id)  # Changed to chat_id room

@socketio.on('join_chat')
def handle_join_chat(data):
    chat_id = data['chat_id']
    user_type = data['user_type']  # 'agent' or 'customer'
    
    join_room(chat_id)
    print(f"{user_type} joined chat {chat_id}")
    
    # Send chat history
    cur = get_db()
    cur.execute(
        "SELECT messages FROM chats WHERE id = %s",
        (chat_id,)
    )
    result = cur.fetchone()
    
    if result and result['messages']:
        messages = json.loads(result['messages'])
        emit('chat_history', {
            'chat_id': chat_id,
            'messages': [{
                'id': f"msg_{i}",
                'content': msg['text'],
                'sender': msg['from'],
                'timestamp': msg['timestamp']
            } for i, msg in enumerate(messages)]
        })

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)