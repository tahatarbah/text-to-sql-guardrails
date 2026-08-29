-- Sample ecommerce schema for Text-to-SQL demos
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    email VARCHAR(180) UNIQUE NOT NULL,
    city VARCHAR(80),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    category VARCHAR(80) NOT NULL,
    price NUMERIC(10, 2) NOT NULL
);

CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date DATE NOT NULL DEFAULT CURRENT_DATE,
    status VARCHAR(40) NOT NULL DEFAULT 'completed'
);

CREATE TABLE order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(10, 2) NOT NULL
);

INSERT INTO customers (name, email, city) VALUES
    ('Ada Lovelace', 'ada@example.com', 'London'),
    ('Alan Turing', 'alan@example.com', 'Manchester'),
    ('Grace Hopper', 'grace@example.com', 'New York'),
    ('Katherine Johnson', 'kathy@example.com', 'Virginia'),
    ('Donald Knuth', 'don@example.com', 'California');

INSERT INTO products (name, category, price) VALUES
    ('Laptop Pro', 'Electronics', 1299.00),
    ('Wireless Mouse', 'Electronics', 29.99),
    ('Standing Desk', 'Furniture', 449.00),
    ('Notebook Pack', 'Office', 12.50),
    ('Mechanical Keyboard', 'Electronics', 89.00);

INSERT INTO orders (customer_id, order_date, status) VALUES
    (1, '2024-11-02', 'completed'),
    (1, '2025-01-15', 'completed'),
    (2, '2025-02-01', 'completed'),
    (3, '2025-02-20', 'shipped'),
    (4, '2025-03-05', 'completed'),
    (5, '2025-03-12', 'cancelled');

INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
    (1, 1, 1, 1299.00),
    (1, 2, 2, 29.99),
    (2, 5, 1, 89.00),
    (3, 3, 1, 449.00),
    (3, 4, 3, 12.50),
    (4, 2, 1, 29.99),
    (5, 1, 1, 1299.00),
    (6, 4, 2, 12.50);
