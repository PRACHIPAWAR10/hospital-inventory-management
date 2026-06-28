from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import csv
import io
import os
app = Flask(__name__)
app.secret_key = "change_this_secret_key"  # change this in production
DB_PATH = "database.db"

# -----------------------
# Database initialization
# -----------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # users
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT
    )''')

    # suppliers
    c.execute('''CREATE TABLE IF NOT EXISTS suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        contact TEXT
    )''')

    # inventory
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        unit TEXT,
        supplier_id INTEGER,
        price REAL,
        expiry_date TEXT,
        reorder_level INTEGER DEFAULT 10,
        date_added TEXT,
        last_updated TEXT,
        FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
    )''')

    conn.commit()
    c.execute('''
CREATE TABLE IF NOT EXISTS stock_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER,
    change_type TEXT,
    quantity INTEGER,
    changed_by TEXT,
    timestamp TEXT
)
''')
    # activity logs
    c.execute('''
    CREATE TABLE IF NOT EXISTS activity_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT,
        action TEXT,
        timestamp TEXT
    )
    ''')

    # create default admin if not exists
    c.execute("SELECT * FROM users WHERE username='admin'")
    if not c.fetchone():
        hashed = generate_password_hash("admin123")
        c.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                  ("admin", hashed, "admin"))
        print("Created default admin -> username: admin password: admin123")

    conn.commit()
    conn.close()

# -----------------------
# Auth / decorators
# -----------------------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
def log_activity(username, action):
    conn = get_db_connection()
    now = datetime.utcnow().isoformat()
    conn.execute("""
        INSERT INTO activity_logs (username, action, timestamp)
        VALUES (?, ?, ?)
    """, (username, action, now))
    conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash("Admin access required.", "warning")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

# -----------------------
# Routes: Authentication
# -----------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        if user and check_password_hash(user["password"], password):
            session['user_id'] = user["id"]
            session['username'] = user["username"]
            session['role'] = user["role"]

            # 🔥 ADD THIS LINE
            log_activity(user["username"], "Logged in")

            flash(f"Welcome, {user['username']}!", "success")
            return redirect(url_for('index'))

        flash("Invalid username or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for('login'))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        role = "staff"  # default role for new users

        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("register"))

        hashed = generate_password_hash(password)

        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO users (username, password, role) VALUES (?, ?, ?)", 
                         (username, hashed, role))
            conn.commit()
            flash("✅ Registration successful! Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("⚠️ Username already exists.", "danger")
        conn.close()

    return render_template("register.html")

# -----------------------
# Routes: Dashboard
# -----------------------
@app.route("/")
@login_required
def index():
    conn = get_db_connection()
    total_items = conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]
    total_quantity = conn.execute("SELECT SUM(quantity) FROM inventory").fetchone()[0] or 0
    low_stock = conn.execute("SELECT COUNT(*) FROM inventory WHERE reorder_level IS NOT NULL AND quantity < reorder_level").fetchone()[0]
    today = datetime.utcnow().date().isoformat()
    expired = conn.execute("SELECT COUNT(*) FROM inventory WHERE expiry_date IS NOT NULL AND expiry_date <> '' AND date(expiry_date) < date(?)", (today,)).fetchone()[0]
    total_value = conn.execute("SELECT SUM(quantity * price) FROM inventory").fetchone()[0] or 0
    conn.close()
    return render_template("index.html",
                       total_items=total_items,
                       total_quantity=total_quantity,
                       low_stock=low_stock,
                       expired=expired,
                       total_value=total_value)

# -----------------------
# Routes: Suppliers
# -----------------------
@app.route("/suppliers")
@login_required
def suppliers():
    conn = get_db_connection()
    rows = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
    conn.close()
    return render_template("suppliers.html", suppliers=rows)

@app.route("/suppliers/add", methods=["POST"])
@admin_required
def add_supplier():
    name = request.form["name"].strip()
    contact = request.form.get("contact", "").strip()
    if not name:
        flash("Supplier name required.", "danger")
        return redirect(url_for('suppliers'))
    conn = get_db_connection()
    conn.execute("INSERT INTO suppliers (name, contact) VALUES (?, ?)", (name, contact))
    conn.commit()
    conn.close()
    flash("Supplier added.", "success")
    return redirect(url_for('suppliers'))

@app.route("/suppliers/delete/<int:supplier_id>")
@admin_required
def delete_supplier(supplier_id):
    conn = get_db_connection()
    used = conn.execute("SELECT COUNT(*) FROM inventory WHERE supplier_id=?", (supplier_id,)).fetchone()[0]
    if used:
        flash("Cannot delete supplier that is linked to inventory items.", "warning")
        conn.close()
        return redirect(url_for('suppliers'))
    conn.execute("DELETE FROM suppliers WHERE id=?", (supplier_id,))
    conn.commit()
    conn.close()
    flash("Supplier deleted.", "info")
    return redirect(url_for('suppliers'))

# -----------------------
# Routes: Inventory
# -----------------------
@app.route("/inventory")
@login_required
def inventory():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    supplier = request.args.get("supplier", "").strip()
    filter_mode = request.args.get("filter", "all")

    base_sql = "SELECT inventory.*, suppliers.name as supplier_name FROM inventory LEFT JOIN suppliers ON inventory.supplier_id = suppliers.id WHERE 1=1"
    params = []

    if q:
        base_sql += " AND (inventory.name LIKE ? OR inventory.category LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if category:
        base_sql += " AND inventory.category = ?"
        params.append(category)
    if supplier:
        base_sql += " AND suppliers.id = ?"
        params.append(supplier)

    today = datetime.utcnow().date().isoformat()
    if filter_mode == "low":
        base_sql += " AND reorder_level IS NOT NULL AND quantity < reorder_level"
    elif filter_mode == "expired":
        base_sql += " AND expiry_date IS NOT NULL AND expiry_date <> '' AND date(expiry_date) < date(?)"
        params.append(today)

    base_sql += " ORDER BY inventory.name COLLATE NOCASE"
    conn = get_db_connection()
    rows = conn.execute(base_sql, params).fetchall()
    suppliers = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
    conn.close()
    categories = sorted({r["category"] for r in rows}) if rows else []
    return render_template("inventory.html", items=rows, suppliers=suppliers, categories=categories, q=q, selected_supplier=supplier, filter_mode=filter_mode)
@app.route("/inventory/add", methods=["POST"])
@admin_required
def add_item():
    form = request.form
    name = form.get("name", "").strip()
    category = form.get("category", "").strip()
    quantity = int(form.get("quantity", 0))
    unit = form.get("unit", "").strip()
    supplier_id = form.get("supplier_id") or None
    price = form.get("price") or None
    expiry_date = form.get("expiry_date") or None
    reorder_level = form.get("reorder_level") or None

    if not name or not category:
        flash("Name & Category required.", "danger")
        return redirect(url_for('inventory'))

    conn = get_db_connection()
    now = datetime.utcnow().isoformat()
    conn.execute("""INSERT INTO inventory 
                    (name, category, quantity, unit, supplier_id, price, expiry_date, reorder_level, date_added, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                 (name, category, quantity, unit, supplier_id, price, expiry_date, reorder_level, now, now))
    conn.commit()
    conn.close()

    
    log_activity(session['username'], f"Added item {name} (Qty: {quantity})")

    flash("Item added.", "success")
    return redirect(url_for('inventory'))

@app.route("/inventory/edit/<int:item_id>", methods=["GET", "POST"])
@admin_required
def edit_item(item_id):
    conn = get_db_connection()
    item = conn.execute("SELECT * FROM inventory WHERE id=?", (item_id,)).fetchone()
    
    if not item:
        conn.close()
        flash("Item not found.", "danger")
        return redirect(url_for('inventory'))

    suppliers = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()

    if request.method == "POST":
        form = request.form
        name = form.get("name", "").strip()
        category = form.get("category", "").strip()
        quantity = int(form.get("quantity", 0))
        old_quantity = int(item["quantity"])
        unit = form.get("unit", "").strip()
        supplier_id = form.get("supplier_id") or None
        price = form.get("price") or None
        expiry_date = form.get("expiry_date") or None
        reorder_level = form.get("reorder_level") or None
        now = datetime.utcnow().isoformat()

        conn.execute("""UPDATE inventory SET
                        name=?, category=?, quantity=?, unit=?, supplier_id=?, price=?, expiry_date=?, reorder_level=?, last_updated=?
                        WHERE id=?""",
                     (name, category, quantity, unit, supplier_id, price, expiry_date, reorder_level, now, item_id))

        difference = quantity - old_quantity

        if difference != 0:
            change_type = "Increased" if difference > 0 else "Decreased"
            conn.execute("""
                INSERT INTO stock_history (item_id, change_type, quantity, changed_by, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (item_id, change_type, abs(difference), session.get("username"), now))

        conn.commit()
        conn.close()
        flash("Item updated.", "success")
        return redirect(url_for('inventory'))

    conn.close()
    return render_template("edit_item.html", item=item, suppliers=suppliers)

@app.route("/inventory/delete/<int:item_id>")
@admin_required
def delete_item(item_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM inventory WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    flash("Item deleted.", "info")
    return redirect(url_for('inventory'))


@app.route("/history/<int:item_id>")
@login_required
def view_history(item_id):
    conn = get_db_connection()
    history = conn.execute("""
        SELECT * FROM stock_history
        WHERE item_id = ?
        ORDER BY timestamp DESC
    """, (item_id,)).fetchall()
    conn.close()

    return render_template("history.html", history=history)

# -----------------------
# Export CSV
# -----------------------
@app.route("/inventory/export")
@login_required
def export_csv():
    conn = get_db_connection()
    rows = conn.execute("SELECT inventory.*, suppliers.name as supplier_name FROM inventory LEFT JOIN suppliers ON inventory.supplier_id = suppliers.id ORDER BY inventory.name").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    header = ["ID","Name","Category","Quantity","Unit","Supplier","Price","Expiry Date","Reorder Level","Date Added","Last Updated"]
    writer.writerow(header)
    for r in rows:
        writer.writerow([r["id"], r["name"], r["category"], r["quantity"], r["unit"], r["supplier_name"], r["price"], r["expiry_date"], r["reorder_level"], r["date_added"], r["last_updated"]])
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode()), 
        mimetype="text/csv", 
        as_attachment=True, 
        download_name="inventory_export.csv"
    )

# -----------------------
# Helper: item status
# -----------------------
def item_status(item_row):
    try:
        qty = int(item_row["quantity"])
    except:
        qty = 0
    reorder = item_row["reorder_level"]
    try:
        reorder = int(reorder) if reorder is not None and reorder != "" else None
    except:
        reorder = None
    expiry = item_row["expiry_date"]
    status = "ok"
    if expiry and expiry != "":
        try:
            exp_date = datetime.fromisoformat(expiry).date()
            today = datetime.utcnow().date()
            if exp_date < today:
                status = "expired"
                return status
            elif exp_date <= today + timedelta(days=30):
                status = "expiring_soon"
        except:
            pass
    if reorder is not None and qty < reorder:
        status = "low" if status == "ok" else status
    return status

app.jinja_env.globals.update(item_status=item_status)

# -----------------------
# Global Low Stock Badge
# -----------------------
@app.context_processor
def inject_low_stock_count():
    if 'user_id' in session:
        conn = get_db_connection()
        low_stock_count = conn.execute("""
            SELECT COUNT(*) FROM inventory
            WHERE reorder_level IS NOT NULL
            AND quantity < reorder_level
        """).fetchone()[0]
        conn.close()
        return dict(low_stock_count=low_stock_count)
    return dict(low_stock_count=0)

# -----------------------
# Run app
# -----------------------   


# -----------------------
# Run app
# -----------------------
if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        init_db()
    else:
        init_db()
    app.run(debug=True)
