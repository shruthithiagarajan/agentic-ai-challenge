import sqlite3, sys, csv
db, month = sys.argv[1], sys.argv[2]
con = sqlite3.connect(db)
w = csv.writer(sys.stdout)
w.writerow(["metric", "value"])
n, v = con.execute("select count(*), round(sum(total),2) from orders where substr(order_date,1,7)=?", (month,)).fetchone()
w.writerow(["orders", n]); w.writerow(["total_value", v])
for s, c in con.execute("select status, count(*) from orders where substr(order_date,1,7)=? group by status", (month,)):
    w.writerow([f"status_{s}", c])
for name, t in con.execute("select c.name, round(sum(o.total),2) from orders o join customers c on c.id=o.customer_id where substr(order_date,1,7)=? group by c.name order by 2 desc limit 5", (month,)):
    w.writerow([f"top_customer:{name}", t])
