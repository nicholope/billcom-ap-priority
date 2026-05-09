"""
Realistic mock bill data for demo purposes (Bill.com field schema).
Simulates a property management company's AP queue.
"""

from datetime import date, timedelta

today = date.today()


def get_mock_bills() -> list[dict]:
    def d(offset: int) -> str:
        return (today + timedelta(days=offset)).isoformat()

    return [
        {
            "id": "BILL-001", "vendorId": "V-101", "vendorName": "Mesa Plumbing & Drain",
            "dueDate": d(-5), "amount": 4800.00, "amountDue": 4800.00,
            "paymentMethodType": "ACH", "approvalStatus": "4", "paymentStatus": "0",
        },
        {
            "id": "BILL-002", "vendorId": "V-102", "vendorName": "Southwest Electric Co.",
            "dueDate": d(-2), "amount": 12300.00, "amountDue": 12300.00,
            "paymentMethodType": "WIRE", "approvalStatus": "4", "paymentStatus": "0",
        },
        {
            "id": "BILL-003", "vendorId": "V-103", "vendorName": "AZ Property Insurance Group",
            "dueDate": d(1), "amount": 28500.00, "amountDue": 28500.00,
            "paymentMethodType": "CHECK", "approvalStatus": "1", "paymentStatus": "0",
        },
        {
            "id": "BILL-004", "vendorId": "V-104", "vendorName": "Desert Landscaping LLC",
            "dueDate": d(3), "amount": 2200.00, "amountDue": 2200.00,
            "paymentMethodType": "ACH", "approvalStatus": "4", "paymentStatus": "0",
        },
        {
            "id": "BILL-005", "vendorId": "V-105", "vendorName": "SRP Electric Utility",
            "dueDate": d(2), "amount": 6750.00, "amountDue": 6750.00,
            "paymentMethodType": "ACH", "approvalStatus": "4", "paymentStatus": "0",
        },
        {
            "id": "BILL-006", "vendorId": "V-106", "vendorName": "Valley Roof & Repair",
            "dueDate": d(-10), "amount": 9100.00, "amountDue": 9100.00,
            "paymentMethodType": "CHECK", "approvalStatus": "1", "paymentStatus": "0",
        },
        {
            "id": "BILL-007", "vendorId": "V-101", "vendorName": "Mesa Plumbing & Drain",
            "dueDate": d(7), "amount": 1350.00, "amountDue": 1350.00,
            "paymentMethodType": "ACH", "approvalStatus": "4", "paymentStatus": "0",
        },
        {
            "id": "BILL-008", "vendorId": "V-107", "vendorName": "Phoenix HVAC Services",
            "dueDate": d(4), "amount": 5500.00, "amountDue": 5500.00,
            "paymentMethodType": "ACH", "approvalStatus": "4", "paymentStatus": "0",
        },
        {
            "id": "BILL-009", "vendorId": "V-108", "vendorName": "Cornerstone Title & Escrow",
            "dueDate": d(0), "amount": 15000.00, "amountDue": 15000.00,
            "paymentMethodType": "WIRE", "approvalStatus": "4", "paymentStatus": "0",
        },
        {
            "id": "BILL-010", "vendorId": "V-109", "vendorName": "Sunstate Equipment Rental",
            "dueDate": d(14), "amount": 3200.00, "amountDue": 3200.00,
            "paymentMethodType": "ACH", "approvalStatus": "1", "paymentStatus": "0",
        },
        {
            "id": "BILL-011", "vendorId": "V-110", "vendorName": "ABC General Contracting",
            "dueDate": d(-1), "amount": 47000.00, "amountDue": 47000.00,
            "paymentMethodType": "WIRE", "approvalStatus": "4", "paymentStatus": "0",
        },
        {
            "id": "BILL-012", "vendorId": "V-110", "vendorName": "ABC General Contracting",
            "dueDate": d(10), "amount": 23000.00, "amountDue": 23000.00,
            "paymentMethodType": "WIRE", "approvalStatus": "1", "paymentStatus": "0",
        },
        {
            "id": "BILL-013", "vendorId": "V-111", "vendorName": "City of Phoenix — Water",
            "dueDate": d(5), "amount": 1800.00, "amountDue": 1800.00,
            "paymentMethodType": "ACH", "approvalStatus": "4", "paymentStatus": "0",
        },
        {
            "id": "BILL-014", "vendorId": "V-112", "vendorName": "Cactus Security Systems",
            "dueDate": d(21), "amount": 4200.00, "amountDue": 4200.00,
            "paymentMethodType": "ACH", "approvalStatus": "4", "paymentStatus": "0",
        },
        {
            "id": "BILL-015", "vendorId": "V-113", "vendorName": "Thunderbird Pest Control",
            "dueDate": d(30), "amount": 850.00, "amountDue": 850.00,
            "paymentMethodType": "ACH", "approvalStatus": "4", "paymentStatus": "0",
        },
    ]
