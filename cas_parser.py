from datetime import datetime
from tabulate import tabulate
from pyxirr import xirr
import pdfplumber
import re
import json
import requests


def get_latest_nav(isin):
   values = requests.get('https://www.amfiindia.com/spages/NAVOpen.txt')
   for entry in values.text.splitlines():
       if isin in entry:
           scheme_code,isin,isin_div,scheme_name,nav,nav_date = entry.strip('\r\n').split(';')
   return (nav,nav_date)

def get_scheme_code(isin):
   values = requests.get('https://www.amfiindia.com/spages/NAVOpen.txt')
   for entry in values.text.splitlines():
       if isin in entry:
           scheme_code,isin,isin_div,scheme_name,nav,nav_date = entry.strip('\r\n').split(';')
   return scheme_code


def clean_text(text):
   text = text.replace(',','')
   text = text.replace('(','')
   text = text.replace(')','')
   text = text.strip()
   return text 

def parse_cas_report(file_path, doc_pwd):
   final_text=''
   fund_details_dict = {}
   transaction_details = {}
   count=0
   transaction_no = 0
   total_cost_value=0
   

   with pdfplumber.open(file_path, password=doc_pwd) as pdf:
     for i in range(len(pdf.pages)):
       txt = pdf.pages[i].extract_text()
       final_text = final_text + "\n" + txt

   for line in final_text.splitlines():
     if re.search('ISIN:',line):
       if count != 0:
          fund_details_dict[fund_name] = {"Transactions": transactions_list}
          fund_details_dict[fund_name].update({"ISIN":  isin_code})
          fund_details_dict[fund_name].update({"SchemeCode": get_scheme_code(isin_code)})
          transaction_details={}          
          transaction_no = 0
          total_cost_value = 0

       isin_code = re.findall("ISIN:\s(\w+)[(\s]", line)[0]
       fund_name = '_'.join(line.split('-')[1].split('(')[0].split())

       transactions_list = []    
       #If fund name already part of the report, append transactions instead of overwriting. 
       if fund_name in fund_details_dict.keys():
           transactions_list = fund_details_dict[fund_name]["Transactions"]

       count+=1
     elif re.search('Total Cost Value:',line):
       line = line.replace(',','')
       total_cost_value = re.search('Total Cost Value:\s(\d+\.\d+)', line).group(1)
     elif 'Purchase' in line or 'Redemption' in line or 'Systematic' in line or 'Lateral Shift' in line or 'Reversal' in line or 'S T P Out' in line or 'S T P In' in line:
       transaction_no += 1
       values = line.split()
       date = values[0]
       units_balance = clean_text(values[-1])
       nav = values[-2]
       units = clean_text(values[-3])
       raw_amt = values[-4]
       amt = clean_text(values[-4])

       description = ' '.join(values[1:-4])
       if re.search('\(*\)',raw_amt):
          transaction_type = 'Sell'
       else:
          transaction_type = 'Buy'
       try:
          float(amt)
          transactions_list.append([isin_code, date, transaction_type, amt, units, nav])
       except ValueError:
          pass

   fund_details_dict[fund_name] = {"Transactions": transactions_list}
   fund_details_dict[fund_name].update({"ISIN":  isin_code })
   fund_details_dict[fund_name].update({"SchemeCode": get_scheme_code(isin_code)})
   
   new_fund_details_dict = remove_rejeted_transactions(fund_details_dict)

   return new_fund_details_dict

def remove_rejeted_transactions(funds_transactions_details):
    new_funds_transactions_details = {}
    for fund in funds_transactions_details:
        transactions_list = funds_transactions_details[fund]["Transactions"]
        isin = funds_transactions_details[fund]["ISIN"]
        scheme_code = funds_transactions_details[fund]["SchemeCode"]

        for transaction_index, transaction in enumerate(transactions_list,start=0):
            isin_code, date, transaction_type, amt, units, nav = transaction
            if transaction_type == 'Sell':
                try:
                    index_of_buy_transaction =  transactions_list.index([isin_code, date, "Buy", amt, units, nav])
                    transactions_list.pop(transaction_index)
                    transactions_list.pop(index_of_buy_transaction)
                except ValueError:
                    continue

        new_funds_transactions_details[fund] = {"Transactions": transactions_list}
        new_funds_transactions_details[fund].update({"ISIN": isin})
        new_funds_transactions_details[fund].update({"SchemeCode": scheme_code})

    return new_funds_transactions_details

def process_sell(sell_units, buy_list):
    new_buy_list = []
    for index, buy_transaction in enumerate(buy_list,start=0):
        isin_code, date, transaction_type, amt, buy_units, nav = buy_transaction
        buy_units = float(buy_units)
        sell_units = float(sell_units)

        if sell_units < buy_units:
            buy_units -= sell_units
            buy_transaction[4] = buy_units
            new_buy_list.append(buy_transaction)
            sell_units = 0
        elif sell_units > buy_units:
            sell_units -= buy_units
            new_buy_list = process_sell(sell_units, new_buy_list)
        elif sell_units == buy_units:
            sell_units = 0
        else:
            new_buy_list.append(buy_transaction)
    return new_buy_list


def calculate_total_invested_amount(transactions):
    buy_transactions = []
    sell_transactions = []
    total_invested_value = 0

    for transaction in transactions:
        isin_code, date, transaction_type, amt, units, nav = transaction
        if transaction_type == 'Buy':
            buy_transactions.append(transaction)
        else:
            sell_transactions.append(transaction)
        
    for sell_transaction in sell_transactions:
        isin_code, date, transaction_type, amt, units, nav = sell_transaction
        buy_transactions = process_sell(units, buy_transactions)

    
    for transaction in buy_transactions:
        isin_code, date, transaction_type, amt, units, nav = transaction
        units = float(units)
        nav = float(nav)
        total_invested_value += units * nav

    return total_invested_value



def calculate_returns(fund_details_dict):
   overall_table=[]
   output_table=[]
   ignore_funds = ['Axis_Liquid_Fund','ICICI_Prudential_Liquid_Fund']
   overall_headers = ['Type','Invested Amt','Valuation Date','Current Value','Profit/Loss','XIRR']
   headers = ['Fund','Invested Amt','Total Units','NAV','NAV Date','Current Value','Profit/Loss','XIRR']
   overall_invested_amount_from_all_funds = 0
   overall_fund_valuation_from_all_funds = 0
   transaction_dates_from_all_funds=[]
   transaction_amts_from_all_funds =[]


   for fund_name in fund_details_dict.keys():
     if fund_name in ignore_funds:
        continue
     total_units = 0
     total_invested_amt = 0
     transaction_dates = []
     transaction_amts = []

     for transaction in fund_details_dict[fund_name]["Transactions"]:
            isin_code, date, transaction_type, amt, units, nav = transaction 
            transaction_dates.append(datetime.strptime(date,'%d-%b-%Y'))
            transaction_dates_from_all_funds.append(datetime.strptime(date,'%d-%b-%Y'))

            if transaction_type == "Buy": 
               total_invested_amt+=float(amt)
               transaction_amts.append(float('-'+amt))
               transaction_amts_from_all_funds.append(float('-'+amt))
               total_units+=float(units)
           
            elif transaction_type == "Sell":
               total_invested_amt-=float(amt)
               transaction_amts.append(float(amt))
               transaction_amts_from_all_funds.append(float(amt))
               total_units-=float(units)

     total_cost_value  = calculate_total_invested_amount(fund_details_dict[fund_name]["Transactions"])

     #nav_values = 
     #if (os.popen("uname").read().strip('\r\n')) == 'Linux':
     #    nav_values = os.popen('grep {0} nav_report.txt'.format(isin_code)).read().strip('\r\n').split(';')
     #else:
     #    nav_values = os.popen('findstr {0} nav_report.txt'.format(isin_code)).read().strip('\r\n').split(';')
     
     nav_value, nav_date = get_latest_nav(isin_code) 
     overall_fund_valuation = float(total_units) * float(nav_value)
     
     transaction_dates.append(datetime.strptime(nav_date,'%d-%b-%Y'))
     transaction_amts.append(overall_fund_valuation)
     
     transaction_dates_from_all_funds.append(datetime.strptime(nav_date,'%d-%b-%Y'))
     transaction_amts_from_all_funds.append(overall_fund_valuation)
     overall_invested_amount_from_all_funds+=float(total_cost_value)
     overall_fund_valuation_from_all_funds+=overall_fund_valuation
     
     profit = round((overall_fund_valuation - float(total_cost_value)),2)
     xirr_return = round(xirr(transaction_dates,transaction_amts)*100,1)
     output_table.append([fund_name,round(total_cost_value,2),round(total_units,2),nav_value,nav_date,round(overall_fund_valuation,2),profit,str(xirr_return)+' %'])


   nav_value, nav_date = get_latest_nav("INF846K01DP8")
   overall_profit = round((overall_fund_valuation_from_all_funds - overall_invested_amount_from_all_funds),2)
   overall_xirr_return = round(xirr(transaction_dates_from_all_funds,transaction_amts_from_all_funds)*100,1)
   overall_table.append(['Overall',round(overall_invested_amount_from_all_funds,2),nav_date,round(overall_fund_valuation_from_all_funds,2),overall_profit,str(overall_xirr_return)+' %'])
   print(tabulate(output_table, headers, tablefmt="psql", floatfmt=".2f"))

   print(tabulate(overall_table, overall_headers, tablefmt="psql", floatfmt=".2f"))
      

if __name__ == '__main__':
  fund_details_dict = parse_cas_report('file_path', 'password') 
  calculate_returns(fund_details_dict) 
