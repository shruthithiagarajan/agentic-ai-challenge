import csv
rows=list(csv.DictReader(open('catalog.csv')))
print(len(rows),'products;', sum(1 for r in rows if int(r['stock'])==0),'out of stock')
