from dataclasses import dataclass
from datetime import datetime, date, timedelta
from pyxirr import xirr
from tabulate import tabulate
import argparse
import cas_parser
import json
import requests



parser = argparse.ArgumentParser()
parser.add_argument('-sd', '--startdate', required=True, help='Start of the valudation date')
parser.add_argument('-ed', '--enddate', required=True, help='End date of the valuation')
parser.add_argument('-sf', '--statementfile', required=True, help='Statement file')
parser.add_argument('-p', '--password', required=True, help='Statement file password')
args = parser.parse_args()

@dataclass
class Transaction:
    fund_name:str
    amt:float
    isin:str
    transaction_date:datetime
    nav:float
    transaction_type:str
    units:float


class TransactionsManager:
    def __init__(self):
    #Load transactions from JSON dump file; 
        self.transactions = []
        self.cas_data = cas_parser.parse_cas_report(args.statementfile, args.password)

        isins = [] 
        for fund in self.cas_data.keys():
            isins.append(self.cas_data[fund]["ISIN"])
            for transaction in self.cas_data[fund]["Transactions"]:
                isin, trans_date, transaction_type, amt, units, nav = transaction 
                amt = float(amt)
                transaction_date = datetime.strptime(trans_date, "%d-%b-%Y")
                nav = float(nav)
                units = float(units)
                self.transactions.append(Transaction(fund, amt, isin, transaction_date, nav, transaction_type, units))
        self.uniq_isins = list(set(isins))
      
        #Load NAV data for the schemes in CAS statement
        self.generate_nav_map()

    def generate_nav_map(self):
        """
        This function pulls the NAV details from mfapi.in and generates a NAV dict for calculation
        """
        nav_dict = {}
        for fund in self.cas_data.keys():
            scheme_code = self.cas_data[fund]["SchemeCode"]
            isin = self.cas_data[fund]["ISIN"]
            nav_for_scheme = requests.get(f"https://api.mfapi.in/mf/{scheme_code}")
            nav_values_for_scheme = json.loads(nav_for_scheme.text)

            for entry in nav_values_for_scheme["data"]:
                nav_date = datetime.strptime(entry["date"], "%d-%m-%Y").strftime("%d-%b-%Y")
                nav = entry["nav"]
                if nav_date in nav_dict.keys():
                    nav_dict[nav_date].update({isin:{"nav":nav}})
                else:
                    nav_dict.update({nav_date: {isin:{"nav":nav}}})
        
        self.navmap = nav_dict

    def get_all_transactions_and_valuation_till_date(self,end_date):
        table_headers = ["Date","Invested Amount","Current Value","Profit", "Daily P/L"]
        output_table = []
        fund_transactions = []
        transaction_dates = []
        transaction_amts = []
        total_units = 0
        isin = ''
       
        start_date = datetime.strptime(args.startdate,"%Y-%m-%d")
        next_day = timedelta(days=1)
        nav_date = ''
        prev_profit = 0
        while start_date <= end_date:
            fund_transactions = []
            for transaction in self.transactions:
                if transaction.transaction_date <= start_date:
                    fund_transactions.append(transaction)

            nav_date = start_date.strftime("%d-%b-%Y")
            if nav_date in self.navmap.keys():
                result = self.get_xirr(fund_transactions, self.navmap,nav_date)
                if result:
                    nav_date, invested_amt, current_valuation, profit = result
                    daily_change = profit - prev_profit
                    prev_profit = profit
                    daily_change = '\33[91m' + str(daily_change) + '\33[0m' if daily_change < 0 else '\33[32m' + str(daily_change) + '\33[0m'
                    output_table.append([nav_date, invested_amt, current_valuation, profit, daily_change])
                else:
                    print(f"Latest NAV not available for all schemes for {nav_date}")
            start_date += next_day
        print(tabulate(output_table, table_headers, tablefmt="psql", floatfmt=".2f"))
        
    def get_xirr(self, transactions, navmap,nav_date):
        transaction_dates=[]
        transaction_amts=[]
        total_invested_amt = 0
        total_val = 0
        total_units = 0
        isin = ''

        funds_dict = {}
        for transaction in transactions:
            if transaction.fund_name in funds_dict.keys():
                existing_values = funds_dict[transaction.fund_name]
                existing_values.append(transaction)
            else:
                funds_dict[transaction.fund_name]=[transaction]
        try:
            for fund in funds_dict.keys():
                fund_transaction_amts = []
                fund_total_value = 0
                total_units=0
                transaction_list_for_total_invested_value_calc = []
                
                for transaction in sorted(funds_dict[fund], key=lambda x: x.transaction_date):
                    amt = -(float(transaction.amt)) if transaction.transaction_type == 'Buy' else float(transaction.amt)
                    isin = transaction.isin
                    transaction_dates.append(transaction.transaction_date)
                    transaction_amts.append(amt)
                    fund_transaction_amts.append(amt)
                    total_units = total_units + transaction.units if transaction.transaction_type == 'Buy' else total_units - transaction.units

                    transaction_list_for_total_invested_value_calc.append([isin, transaction.transaction_date, transaction.transaction_type, amt, transaction.units, transaction.nav])
                
                total_val +=  float(total_units) * float(navmap[nav_date][isin]["nav"])
                fund_total_value = float(total_units) * float(navmap[nav_date][isin]["nav"])
                transaction_dates.append(datetime.strptime(nav_date,'%d-%b-%Y'))
                transaction_amts.append(fund_total_value)

                #Calculate Total Invested Amout
                
                total_invested_amt += cas_parser.calculate_total_invested_amount(transaction_list_for_total_invested_value_calc) 
            return (nav_date, round(total_invested_amt), round(total_val), round(total_val-total_invested_amt))
        except KeyError:
            return None

if __name__ == '__main__':
    print(f"Valuations For The Period Selected {args.startdate} to {args.enddate}: ")
    tm = TransactionsManager()
    tm.get_all_transactions_and_valuation_till_date(datetime.strptime(args.enddate, "%Y-%m-%d"))
    print("\nLatest Valuation:")
    cas_parser.calculate_returns(tm.cas_data)
