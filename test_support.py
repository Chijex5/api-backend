# bulk_insert_agents.py
import os
import MySQLdb
from uuid import uuid4
from dotenv import load_dotenv

load_dotenv()

# Sample agent data
agents = [
    {
        "name": "Sarah Johnson",
        "email": "sarah.j@shopnex.com",
        "phone": "+1-555-123-4567",
        "role": "Senior Support Specialist"
    },
    {
        "name": "Michael Chen",
        "email": "michael.c@shopnex.com",
        "phone": "+1-555-234-5678",
        "role": "Technical Support Lead"
    },
    {
        "name": "Emma Wilson",
        "email": "emma.w@shopnex.com",
        "phone": "+1-555-345-6789",
        "role": "Customer Success Manager"
    },
    {
        "name": "David Martinez",
        "email": "david.m@shopnex.com",
        "phone": "+1-555-456-7890",
        "role": "Billing Specialist"
    },
    {
        "name": "Lisa Nguyen",
        "email": "lisa.n@shopnex.com",
        "phone": "+1-555-567-8901",
        "role": "Escalation Manager"
    },
    {
        "name": "James Thompson",
        "email": "james.t@shopnex.com",
        "phone": "+1-555-678-9012",
        "role": "Support Technician"
    },
    {
        "name": "Olivia Brown",
        "email": "olivia.b@shopnex.com",
        "phone": "+1-555-789-0123",
        "role": "Quality Assurance Analyst"
    },
    {
        "name": "Daniel Kim",
        "email": "daniel.k@shopnex.com",
        "phone": "+1-555-890-1234",
        "role": "Chat Support Specialist"
    },
    {
        "name": "Sophia Rodriguez",
        "email": "sophia.r@shopnex.com",
        "phone": "+1-555-901-2345",
        "role": "Training Coordinator"
    },
    {
        "name": "Ethan Patel",
        "email": "ethan.p@shopnex.com",
        "phone": "+1-555-012-3456",
        "role": "Night Shift Supervisor"
    }
]

def bulk_insert_agents():
    # Database connection
    conn = MySQLdb.connect(
        host=os.getenv('MYSQL_HOST'),
        user=os.getenv('MYSQL_USER'),
        password=os.getenv('MYSQL_PASSWORD'),
        database=os.getenv('MYSQL_DB'),
        port=int(os.getenv('MYSQL_PORT', 3306))
    )
    
    cursor = conn.cursor()

    # Prepare data for insertion
    agent_records = []
    for agent in agents:
        agent_records.append((
            str(uuid4()),        # Generate unique ID
            agent['name'],
            agent['email'],
            False,              # online status
            'available',        # initial status
            None                # current_chat
        ))

    # SQL insert statement
    query = """
    INSERT INTO agents 
        (id, name, email, online, status, current_chat)
    VALUES (%s, %s, %s, %s, %s, %s)
    """

    try:
        cursor.executemany(query, agent_records)
        conn.commit()
        print(f"Successfully inserted {len(agents)} agents")
    except Exception as e:
        conn.rollback()
        print(f"Error inserting agents: {str(e)}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    bulk_insert_agents()