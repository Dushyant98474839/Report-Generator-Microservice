
# Python program to read CSV file line by line 
# import necessary packages 
import csv 
  
# Open file  
with open('input.csv') as file_obj: 
      
    # Create reader object by passing the file  
    # object to reader method 
    reader_obj = csv.reader(file_obj) 
      
    # Iterate over each row in the csv  
    # file using reader object 
    c=0
    for row in reader_obj: 
        # print(row)
        c+=1
        # break

    print(c)
