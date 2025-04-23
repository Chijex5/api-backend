from flask import Flask, request, jsonify
import json
from datetime import datetime
import os
from flask_cors import CORS
from google import genai
import re
from dotenv import load_dotenv
import html

app = Flask(__name__)
CORS(app)
load_dotenv()

# 🔑 Configure your Gemini API key
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

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
    if len(message) > 15:  # Corrected from message.length to len(message)
        return False
    return any(re.search(pattern, message) for pattern in greetings)

# 🧠 Step 5: Detect Complex Queries or Incomplete Information
def needs_escalation_or_clarification(message, customer, order=None):
    message = message.lower().strip()
    
    # Check for incomplete information (e.g., vague or missing details)
    vague_phrases = ['help', 'issue', 'problem', 'something wrong', 'not working']
    if any(phrase in message for phrase in vague_phrases) and len(message.split()) < 10:
        return {'needs_clarification': True, 'is_escalating': False}
    
    # Check for complex queries that exceed AI capability
    complex_keywords = ['legal', 'lawsuit', 'fraud', 'account hacked', 'bulk order', 'custom order']
    if any(keyword in message for keyword in complex_keywords):
        return {'needs_clarification': False, 'is_escalating': True}
    
    # If message is too short or lacks context
    if len(message.split()) < 3 and not is_greeting(message):
        return {'needs_clarification': True, 'is_escalating': False}
    
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

# 🔁 AI Endpoint: Simulate Customer Support Message
@app.route('/support', methods=['POST'])
def support():
    try:
        data = request.get_json()
        identifier = data.get('identifier')
        message = data.get('message', '').lower()

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
            response_text = f"Hey {customer['name']}, we couldn't find any orders on your account. Did you use another email or phone number?"
            return jsonify({
                'ai_response': {
                    'raw': response_text,
                    'formatted': f"<div>{response_text}</div>"
                },
                'is_escalating': False
            })

        last_order = customer_orders[-1]
        payment = next((p for p in payments if p['paymentid'] == last_order['paymentid']), None)
        product = next((p for p in products if p['product_id'] == last_order['product']), None)

        # Handle simple greetings
        if is_greeting(message):
            greeting_text = f"Hi {customer['name']}! Thanks for reaching out. How can I assist you today?"
            return jsonify({
                'ai_response': {
                    'raw': greeting_text,
                    'formatted': f"<div>{greeting_text}</div>"
                },
                'is_escalating': False
            })

        # Check if the query needs escalation or clarification
        escalation_check = needs_escalation_or_clarification(message, customer, last_order)
        
        if escalation_check['needs_clarification']:
            clarification_text = f"Hi {customer['name']}, could you please provide more details about your issue so I can assist you better?"
            return jsonify({
                'ai_response': {
                    'raw': clarification_text,
                    'formatted': f"<div>{clarification_text}</div>"
                },
                'is_escalating': False
            })
        
        if escalation_check['is_escalating']:
            escalation_text = f"Hi {customer['name']}, it looks like your request requires special attention. I'm escalating this to one of our agents who will assist you shortly."
            return jsonify({
                'ai_response': {
                    'raw': escalation_text,
                    'formatted': f"<div>{escalation_text}</div>"
                },
                'is_escalating': True
            })

        # Proceed with AI response for valid queries
        ai_response = generate_ai_response(customer, message, order=last_order, payment=payment, product=product)

        return jsonify({
            'ai_response': ai_response,
            'is_escalating': False
        })
    except Exception as e:
        print(e)
        error_text = "Sorry, something went wrong while processing your request. Please try again later."
        return jsonify({
            'ai_response': {
                'raw': error_text,
                'formatted': f"<div>{error_text}</div>"
            },
            'is_escalating': False
        })

if __name__ == '__main__':
    app.run(debug=True, port=5000)