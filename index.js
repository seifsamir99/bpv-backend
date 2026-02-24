require('dotenv').config();
const express = require('express');
const cors = require('cors');
const { google } = require('googleapis');
const Anthropic = require('@anthropic-ai/sdk');

const app = express();
app.use(cors());
app.use(express.json());

// Initialize Google Sheets API
const getAuth = () => {
  const email = process.env.GOOGLE_SERVICE_ACCOUNT_EMAIL;
  let privateKey = process.env.GOOGLE_PRIVATE_KEY;

  if (privateKey) {
    privateKey = privateKey.replace(/\\n/g, '\n');
  }

  const auth = new google.auth.GoogleAuth({
    credentials: {
      client_email: email,
      private_key: privateKey,
    },
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  return auth;
};

const getSheets = async () => {
  const auth = getAuth();
  return google.sheets({ version: 'v4', auth });
};

const SHEET_ID = process.env.GOOGLE_SHEET_ID || '1DZfS3J6Men-XuX11nXAYnMhgQKCn4SJTFDDFxlEQAwk';
const SHEET_NAME = 'BPV';
const ALL_BPV_SHEET = 'all Bpv';

// Salary/Employee sheets (for payroll system)
const SALARY_SHEET_ID = process.env.SALARY_SHEET_ID || '1WpLT7IK1x5Pm5Dzj-q98oMCYrzHnFbHMT0DDJN4m7k4';
const LABOUR_SALARY_SHEET = 'Labour salary';
const STAFF_SALARY_SHEET = 'Staff salary';

// Attendance sheet
const ATTENDANCE_SHEET_ID = process.env.ATTENDANCE_SHEET_ID || '1NjZxG_LctqXZP2nk1HvXbFG4rVVzeK6H4WZ-4iRtODE';
const LABOUR_ATTENDANCE_SHEET = 'Labour attendence';
const STAFF_ATTENDANCE_SHEET = 'Staff attendence';
const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// PDC (Post-Dated Cheques) tracking sheet
const PDC_SHEET_ID = process.env.PDC_SHEET_ID || '198RUJOsQf2XbLKM4S1C_OPyznT_Fsrn1JuH_isYLSlA';
const PDC_SHEET_NAME = 'PDC Tracker';

// Convert 1-based column number to letter(s): 1=A, 26=Z, 27=AA, 34=AH
function getColumnLetter(colNum) {
  let letter = '';
  while (colNum > 0) {
    const mod = (colNum - 1) % 26;
    letter = String.fromCharCode(65 + mod) + letter;
    colNum = Math.floor((colNum - 1) / 26);
  }
  return letter;
}

// Normalize known attendance statuses to canonical casing
const CANONICAL_STATUSES = {
  present: 'Present', absent: 'Absent', leave: 'Leave',
  off: 'Off', sick: 'Sick', joined: 'Joined', holiday: 'Holiday'
};
function normalizeStatus(status) {
  if (!status) return status;
  return CANONICAL_STATUSES[status.toLowerCase()] || status;
}

// Get or create monthly attendance sheet (e.g., "Labour attendence Jan 2026")
async function getOrCreateMonthlySheet(sheets, baseSheetName, month, year) {
  const monthName = MONTH_NAMES[month];
  const targetSheetName = `${baseSheetName} ${monthName} ${year}`;

  // Get all sheets in the spreadsheet
  const spreadsheet = await sheets.spreadsheets.get({
    spreadsheetId: ATTENDANCE_SHEET_ID,
    fields: 'sheets.properties'
  });

  const existingSheets = spreadsheet.data.sheets.map(s => s.properties.title);

  // If monthly sheet exists, return it
  if (existingSheets.includes(targetSheetName)) {
    return targetSheetName;
  }

  // Find the base sheet ID to copy from
  const baseSheet = spreadsheet.data.sheets.find(s => s.properties.title === baseSheetName);
  if (!baseSheet) throw new Error(`Base sheet ${baseSheetName} not found`);

  // Copy the base sheet
  const copyResponse = await sheets.spreadsheets.sheets.copyTo({
    spreadsheetId: ATTENDANCE_SHEET_ID,
    sheetId: baseSheet.properties.sheetId,
    requestBody: { destinationSpreadsheetId: ATTENDANCE_SHEET_ID }
  });

  // Rename the copied sheet
  await sheets.spreadsheets.batchUpdate({
    spreadsheetId: ATTENDANCE_SHEET_ID,
    requestBody: {
      requests: [{
        updateSheetProperties: {
          properties: {
            sheetId: copyResponse.data.sheetId,
            title: targetSheetName
          },
          fields: 'title'
        }
      }]
    }
  });

  // Clear day columns (D2:AH) to start fresh - keep header row and employee info
  await sheets.spreadsheets.values.clear({
    spreadsheetId: ATTENDANCE_SHEET_ID,
    range: `'${targetSheetName}'!D2:AH`
  });

  return targetSheetName;
}

// Build voucher position map
const buildVoucherPositionMap = () => {
  const positions = {};

  const oldFormatBases = [5, 28, 48, 68, 88, 108, 128, 148, 168];
  oldFormatBases.forEach((base, i) => {
    positions[i + 1] = { bpvNoRow: base, dateRow: base + 1, dataRow: base + 7, totalRow: base + 9, format: 'old' };
  });

  const transitional = [[10, 194], [11, 221], [12, 241], [13, 261], [14, 287], [15, 313], [16, 338], [17, 366]];
  transitional.forEach(([bpvNum, base]) => {
    positions[bpvNum] = { bpvNoRow: base, dateRow: base + 1, dataRow: base + 7, totalRow: base + 9, format: 'transitional' };
  });

  // Extended to support 100 vouchers (BPV 18-117)
  for (let i = 0; i < 100; i++) {
    const base = 393 + (i * 27);
    positions[18 + i] = { bpvNoRow: base, dateRow: base + 1, dataRow: base + 7, totalRow: base + 9, format: 'new' };
  }

  return positions;
};

const VOUCHER_POSITIONS = buildVoucherPositionMap();

const formatDate = (value) => {
  if (!value) return '';
  if (typeof value === 'number') {
    const date = new Date((value - 25569) * 86400 * 1000);
    return date.toLocaleDateString('en-GB');
  }
  return String(value);
};

const parseAmount = (value) => {
  if (!value) return 0;
  if (typeof value === 'number') return value;
  return parseFloat(String(value).replace(/,/g, '')) || 0;
};

const getCell = (rows, rowIdx, colIdx) => {
  if (rowIdx < rows.length && colIdx < rows[rowIdx].length) return rows[rowIdx][colIdx];
  return '';
};

const parseVoucherAtPosition = (rows, bpvNum, pos) => {
  const bpvNoRow = pos.bpvNoRow - 1;
  const dateRow = pos.dateRow - 1;
  const dataRow = pos.dataRow - 1;
  const totalRow = pos.totalRow - 1;

  const bpvNo = getCell(rows, bpvNoRow, 3);
  const date = getCell(rows, dateRow, 3);
  const pdcType = getCell(rows, bpvNoRow, 4) || 'PDC';

  const lineItems = [];
  const skipLabels = ['TOTAL AMOUNT', 'Prepared By', 'Received By', 'Approved By', 'Checked By', '___'];

  for (let i = 0; i < 5; i++) {
    const itemRow = dataRow + i;
    const description = getCell(rows, itemRow, 1);
    if (skipLabels.some(label => String(description).includes(label))) continue;

    const debit = getCell(rows, itemRow, 5);
    const credit = getCell(rows, itemRow, 6);
    const companyName = getCell(rows, itemRow, 2);

    if ((Boolean(companyName) || Boolean(description)) && (parseAmount(debit) > 0 || parseAmount(credit) > 0)) {
      lineItems.push({
        srNo: getCell(rows, itemRow, 0) || String(lineItems.length + 1),
        description, companyName,
        chequeNo: getCell(rows, itemRow, 3),
        chequeDate: formatDate(getCell(rows, itemRow, 4)),
        debit: parseAmount(debit),
        credit: parseAmount(credit)
      });
    }
  }

  const totalAmount = parseAmount(getCell(rows, totalRow, 2));
  return {
    id: bpvNum, bpvNo, date: formatDate(date), pdcType, lineItems, totalAmount,
    hasData: lineItems.length > 0 || totalAmount > 0, baseRow: pos.dataRow, format: pos.format
  };
};

// API Routes
app.get('/api/bpv', async (req, res) => {
  try {
    const sheets = await getSheets();
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${SHEET_NAME}'!A1:G2000`,
    });

    const rows = response.data.values || [];
    const vouchers = [];

    for (const [bpvNumStr, pos] of Object.entries(VOUCHER_POSITIONS)) {
      try {
        const voucher = parseVoucherAtPosition(rows, parseInt(bpvNumStr), pos);
        if (voucher && voucher.hasData) vouchers.push(voucher);
      } catch (error) {
        console.error(`Error parsing BPV #${bpvNumStr}:`, error);
      }
    }

    res.json({ success: true, data: vouchers });
  } catch (error) {
    console.error('Error:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

app.get('/api/bpv/next-number', async (req, res) => {
  try {
    const sheets = await getSheets();
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${SHEET_NAME}'!A1:G2000`,
    });

    const rows = response.data.values || [];
    const vouchers = [];

    for (const [bpvNumStr, pos] of Object.entries(VOUCHER_POSITIONS)) {
      try {
        const voucher = parseVoucherAtPosition(rows, parseInt(bpvNumStr), pos);
        if (voucher && voucher.hasData) vouchers.push(voucher);
      } catch (error) {}
    }

    const bpvNumbers = vouchers.map(v => parseInt(v.bpvNo)).filter(n => !isNaN(n));
    const nextNumber = bpvNumbers.length > 0 ? Math.max(...bpvNumbers) + 1 : 1;

    res.json({ success: true, data: { nextNumber } });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

app.get('/api/bpv/:id', async (req, res) => {
  try {
    const bpvNum = parseInt(req.params.id);
    const pos = VOUCHER_POSITIONS[bpvNum];

    if (!pos) return res.status(404).json({ success: false, error: 'Voucher not found' });

    const sheets = await getSheets();
    const startRow = pos.bpvNoRow - 5;
    const endRow = pos.totalRow + 10;

    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${SHEET_NAME}'!A${startRow}:G${endRow}`,
    });

    const rows = response.data.values || [];
    const adjustedPos = {
      bpvNoRow: pos.bpvNoRow - startRow + 1,
      dateRow: pos.dateRow - startRow + 1,
      dataRow: pos.dataRow - startRow + 1,
      totalRow: pos.totalRow - startRow + 1,
      format: pos.format
    };

    const voucher = parseVoucherAtPosition(rows, bpvNum, adjustedPos);
    res.json({ success: true, data: voucher });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

const calculateTotal = (lineItems) => {
  return Math.round((lineItems || []).reduce((sum, item) => sum + (parseAmount(item.debit) || 0), 0) * 100) / 100;
};

const syncToAllBpv = async (bpvNum, voucherData) => {
  const sheets = await getSheets();
  const response = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A:A` });
  const rows = response.data.values || [];

  let targetRow = -1;
  for (let i = 0; i < rows.length; i++) {
    if (rows[i][0] && parseInt(rows[i][0]) === bpvNum) { targetRow = i + 1; break; }
  }

  const firstItem = voucherData.lineItems?.[0] || {};
  const rowData = [[
    voucherData.bpvNo || bpvNum, firstItem.companyName || '', firstItem.description || '',
    voucherData.date || '', firstItem.chequeNo || '', firstItem.chequeDate || '',
    voucherData.totalAmount || '', voucherData.pdcType || 'PDC'
  ]];

  if (targetRow > 0) {
    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A${targetRow}:H${targetRow}`,
      valueInputOption: 'USER_ENTERED', requestBody: { values: rowData }
    });
  } else {
    await sheets.spreadsheets.values.append({
      spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A:H`,
      valueInputOption: 'USER_ENTERED', insertDataOption: 'INSERT_ROWS', requestBody: { values: rowData }
    });
  }
};

app.post('/api/bpv', async (req, res) => {
  try {
    const sheets = await getSheets();
    const response = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A1:G2000` });
    const rows = response.data.values || [];

    // Find empty slot
    const usedSlots = new Set();
    for (const [bpvNumStr, pos] of Object.entries(VOUCHER_POSITIONS)) {
      const voucher = parseVoucherAtPosition(rows, parseInt(bpvNumStr), pos);
      if (voucher && voucher.hasData) usedSlots.add(parseInt(bpvNumStr));
    }

    let emptySlot = null;
    for (const bpvNum of Object.keys(VOUCHER_POSITIONS).map(k => parseInt(k)).sort((a, b) => a - b)) {
      if (!usedSlots.has(bpvNum)) { emptySlot = bpvNum; break; }
    }

    if (!emptySlot) return res.status(400).json({ success: false, error: 'No empty slots available' });

    // Update the slot
    const pos = VOUCHER_POSITIONS[emptySlot];
    const data = req.body;
    const requests = [];

    if (data.bpvNo !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.bpvNoRow}`, values: [[data.bpvNo]] });
    if (data.date !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.dateRow}`, values: [[data.date]] });
    if (data.pdcType !== undefined) requests.push({ range: `'${SHEET_NAME}'!E${pos.bpvNoRow}`, values: [[data.pdcType]] });

    if (data.lineItems) {
      const lineItems = data.lineItems.slice(0, 5);
      for (let i = 0; i < lineItems.length; i++) {
        const item = lineItems[i];
        requests.push({
          range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`,
          values: [[item.srNo || String(i + 1), item.description || '', item.companyName || '', item.chequeNo || '', item.chequeDate || '', item.debit || '', item.credit || '']]
        });
      }
      for (let i = lineItems.length; i < 5; i++) {
        requests.push({ range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`, values: [['', '', '', '', '', '', '']] });
      }
    }

    const total = data.totalAmount !== undefined ? data.totalAmount : calculateTotal(data.lineItems);
    requests.push({ range: `'${SHEET_NAME}'!C${pos.totalRow}`, values: [[total]] });

    if (requests.length > 0) {
      await sheets.spreadsheets.values.batchUpdate({
        spreadsheetId: SHEET_ID, requestBody: { data: requests, valueInputOption: 'USER_ENTERED' }
      });
    }

    // Get updated voucher and sync
    const getResponse = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A${pos.bpvNoRow - 5}:G${pos.totalRow + 10}` });
    const adjustedPos = { bpvNoRow: 6, dateRow: 7, dataRow: 13, totalRow: 15, format: pos.format };
    const voucher = parseVoucherAtPosition(getResponse.data.values || [], emptySlot, adjustedPos);

    await syncToAllBpv(emptySlot, voucher);

    res.status(201).json({ success: true, data: voucher });
  } catch (error) {
    console.error('Create error:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

app.put('/api/bpv/:id', async (req, res) => {
  try {
    const bpvNum = parseInt(req.params.id);
    const pos = VOUCHER_POSITIONS[bpvNum];
    if (!pos) return res.status(404).json({ success: false, error: 'Voucher not found' });

    const sheets = await getSheets();
    const data = req.body;
    const requests = [];

    if (data.bpvNo !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.bpvNoRow}`, values: [[data.bpvNo]] });
    if (data.date !== undefined) requests.push({ range: `'${SHEET_NAME}'!D${pos.dateRow}`, values: [[data.date]] });
    if (data.pdcType !== undefined) requests.push({ range: `'${SHEET_NAME}'!E${pos.bpvNoRow}`, values: [[data.pdcType]] });

    if (data.lineItems) {
      const lineItems = data.lineItems.slice(0, 5);
      for (let i = 0; i < lineItems.length; i++) {
        const item = lineItems[i];
        requests.push({
          range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`,
          values: [[item.srNo || String(i + 1), item.description || '', item.companyName || '', item.chequeNo || '', item.chequeDate || '', item.debit || '', item.credit || '']]
        });
      }
      for (let i = lineItems.length; i < 5; i++) {
        requests.push({ range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`, values: [['', '', '', '', '', '', '']] });
      }
    }

    if (data.totalAmount !== undefined) {
      requests.push({ range: `'${SHEET_NAME}'!C${pos.totalRow}`, values: [[data.totalAmount]] });
    } else if (data.lineItems) {
      requests.push({ range: `'${SHEET_NAME}'!C${pos.totalRow}`, values: [[calculateTotal(data.lineItems)]] });
    }

    if (requests.length > 0) {
      await sheets.spreadsheets.values.batchUpdate({
        spreadsheetId: SHEET_ID, requestBody: { data: requests, valueInputOption: 'USER_ENTERED' }
      });
    }

    // Get updated voucher
    const startRow = pos.bpvNoRow - 5;
    const getResponse = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A${startRow}:G${pos.totalRow + 10}` });
    const adjustedPos = {
      bpvNoRow: pos.bpvNoRow - startRow + 1, dateRow: pos.dateRow - startRow + 1,
      dataRow: pos.dataRow - startRow + 1, totalRow: pos.totalRow - startRow + 1, format: pos.format
    };
    const voucher = parseVoucherAtPosition(getResponse.data.values || [], bpvNum, adjustedPos);

    await syncToAllBpv(bpvNum, voucher);

    res.json({ success: true, data: voucher });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

app.delete('/api/bpv/:id', async (req, res) => {
  try {
    const bpvNum = parseInt(req.params.id);
    const pos = VOUCHER_POSITIONS[bpvNum];
    if (!pos) return res.status(404).json({ success: false, error: 'Voucher not found' });

    const sheets = await getSheets();

    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!D${pos.bpvNoRow}:E${pos.bpvNoRow}`,
      valueInputOption: 'RAW', requestBody: { values: [['', '']] }
    });

    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!D${pos.dateRow}`,
      valueInputOption: 'RAW', requestBody: { values: [['']] }
    });

    for (let i = 0; i < 5; i++) {
      await sheets.spreadsheets.values.update({
        spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!A${pos.dataRow + i}:G${pos.dataRow + i}`,
        valueInputOption: 'RAW', requestBody: { values: [[String(i + 1), '', '', '', '', '', '']] }
      });
    }

    await sheets.spreadsheets.values.update({
      spreadsheetId: SHEET_ID, range: `'${SHEET_NAME}'!C${pos.totalRow}`,
      valueInputOption: 'RAW', requestBody: { values: [['']] }
    });

    // Clear from all Bpv sheet
    const response = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A:A` });
    const rows = response.data.values || [];
    for (let i = 0; i < rows.length; i++) {
      if (rows[i][0] && parseInt(rows[i][0]) === bpvNum) {
        await sheets.spreadsheets.values.update({
          spreadsheetId: SHEET_ID, range: `'${ALL_BPV_SHEET}'!A${i + 1}:H${i + 1}`,
          valueInputOption: 'RAW', requestBody: { values: [['', '', '', '', '', '', '', '']] }
        });
        break;
      }
    }

    res.json({ success: true, message: `Voucher #${bpvNum} deleted` });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

// ============================================
// EMPLOYEE API ENDPOINTS (for Salary/Payroll)
// ============================================

// Get all employees (Labour + Staff)
app.get('/api/employees', async (req, res) => {
  try {
    const sheets = await getSheets();
    const type = req.query.type; // 'labour', 'staff', or undefined for all

    const employees = [];

    // Fetch Labour employees
    if (!type || type === 'labour') {
      const labourResponse = await sheets.spreadsheets.values.get({
        spreadsheetId: SALARY_SHEET_ID,
        range: `'${LABOUR_SALARY_SHEET}'!A:H`,
      });
      const labourRows = labourResponse.data.values || [];

      // Skip header row, parse employees
      for (let i = 1; i < labourRows.length; i++) {
        const row = labourRows[i];
        if (row[0] && row[1]) { // Must have Employee ID and Name
          employees.push({
            id: row[0],
            employeeId: row[0],
            name: row[1],
            designation: row[2] || '',
            ratePerDay: parseFloat(row[3]) || 0,
            ratePerHour: parseFloat(row[4]) || 0,
            otHours: parseFloat(row[5]) || 0,
            netSalary: parseFloat(row[6]) || 0,
            type: row[7] || 'Labour',
            rowIndex: i + 1, // 1-indexed for Google Sheets
            sheetType: 'labour'
          });
        }
      }
    }

    // Fetch Staff employees
    if (!type || type === 'staff') {
      const staffResponse = await sheets.spreadsheets.values.get({
        spreadsheetId: SALARY_SHEET_ID,
        range: `'${STAFF_SALARY_SHEET}'!A:G`,
      });
      const staffRows = staffResponse.data.values || [];

      for (let i = 1; i < staffRows.length; i++) {
        const row = staffRows[i];
        if (row[0] && row[1]) {
          employees.push({
            id: row[0],
            employeeId: row[0],
            name: row[1],
            ratePerDay: parseFloat(row[2]) || 0,
            designation: row[3] || '',
            deductions: parseFloat(row[4]) || 0,
            netSalary: parseFloat(row[5]) || 0,
            type: row[6] || 'Staff',
            rowIndex: i + 1,
            sheetType: 'staff'
          });
        }
      }
    }

    res.json({ success: true, data: employees });
  } catch (error) {
    console.error('Error fetching employees:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Validate data consistency between salary and attendance sheets
// IMPORTANT: This must be defined BEFORE /api/employees/:id to avoid route conflicts
app.get('/api/employees/validation', async (req, res) => {
  try {
    const sheets = await getSheets();
    const issues = [];

    // Get all employees from salary sheets
    const salaryEmployees = new Map();

    const labourSalaryRes = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:C`,
    });
    (labourSalaryRes.data.values || []).slice(1).forEach(row => {
      if (row[0]) salaryEmployees.set(row[0], { name: row[1], type: 'labour', inSalary: true });
    });

    const staffSalaryRes = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${STAFF_SALARY_SHEET}'!A:C`,
    });
    (staffSalaryRes.data.values || []).slice(1).forEach(row => {
      if (row[0]) salaryEmployees.set(row[0], { name: row[1], type: 'staff', inSalary: true });
    });

    // Get all employees from attendance sheets
    const attendanceEmployees = new Map();

    const labourAttRes = await sheets.spreadsheets.values.get({
      spreadsheetId: ATTENDANCE_SHEET_ID,
      range: `'${LABOUR_ATTENDANCE_SHEET}'!A:C`,
    });
    (labourAttRes.data.values || []).slice(1).forEach(row => {
      if (row[0]) attendanceEmployees.set(row[0], { name: row[1], designation: row[2], type: 'labour', inAttendance: true });
    });

    const staffAttRes = await sheets.spreadsheets.values.get({
      spreadsheetId: ATTENDANCE_SHEET_ID,
      range: `'${STAFF_ATTENDANCE_SHEET}'!A:C`,
    });
    (staffAttRes.data.values || []).slice(1).forEach(row => {
      if (row[0]) attendanceEmployees.set(row[0], { name: row[1], designation: row[2], type: 'staff', inAttendance: true });
    });

    // Find inconsistencies
    // 1. In attendance but not in salary
    attendanceEmployees.forEach((attData, id) => {
      if (!salaryEmployees.has(id)) {
        issues.push({
          type: 'missing_salary',
          employeeId: id,
          name: attData.name,
          designation: attData.designation,
          attendanceType: attData.type,
          message: `Employee ${attData.name} (ID: ${id}) exists in ${attData.type} attendance but not in salary sheet`
        });
      }
    });

    // 2. In salary but not in attendance
    salaryEmployees.forEach((salData, id) => {
      if (!attendanceEmployees.has(id)) {
        issues.push({
          type: 'missing_attendance',
          employeeId: id,
          name: salData.name,
          salaryType: salData.type,
          message: `Employee ${salData.name} (ID: ${id}) exists in ${salData.type} salary but not in attendance sheet`
        });
      }
    });

    // 3. Type mismatch (in both but different types)
    attendanceEmployees.forEach((attData, id) => {
      const salData = salaryEmployees.get(id);
      if (salData && attData.type !== salData.type) {
        issues.push({
          type: 'type_mismatch',
          employeeId: id,
          name: attData.name,
          attendanceType: attData.type,
          salaryType: salData.type,
          message: `Employee ${attData.name} (ID: ${id}) is ${attData.type} in attendance but ${salData.type} in salary`
        });
      }
    });

    res.json({
      success: true,
      totalIssues: issues.length,
      issues
    });
  } catch (error) {
    console.error('Error validating employees:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Get single employee
app.get('/api/employees/:id', async (req, res) => {
  try {
    const employeeId = req.params.id;
    const sheets = await getSheets();

    // Search in Labour sheet
    const labourResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:H`,
    });
    const labourRows = labourResponse.data.values || [];

    for (let i = 1; i < labourRows.length; i++) {
      const row = labourRows[i];
      if (row[0] === employeeId) {
        return res.json({
          success: true,
          data: {
            id: row[0],
            employeeId: row[0],
            name: row[1],
            designation: row[2] || '',
            ratePerDay: parseFloat(row[3]) || 0,
            ratePerHour: parseFloat(row[4]) || 0,
            otHours: parseFloat(row[5]) || 0,
            netSalary: parseFloat(row[6]) || 0,
            type: row[7] || 'Labour',
            rowIndex: i + 1,
            sheetType: 'labour'
          }
        });
      }
    }

    // Search in Staff sheet
    const staffResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${STAFF_SALARY_SHEET}'!A:G`,
    });
    const staffRows = staffResponse.data.values || [];

    for (let i = 1; i < staffRows.length; i++) {
      const row = staffRows[i];
      if (row[0] === employeeId) {
        return res.json({
          success: true,
          data: {
            id: row[0],
            employeeId: row[0],
            name: row[1],
            ratePerDay: parseFloat(row[2]) || 0,
            designation: row[3] || '',
            deductions: parseFloat(row[4]) || 0,
            netSalary: parseFloat(row[5]) || 0,
            type: row[6] || 'Staff',
            rowIndex: i + 1,
            sheetType: 'staff'
          }
        });
      }
    }

    res.status(404).json({ success: false, error: 'Employee not found' });
  } catch (error) {
    console.error('Error fetching employee:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Get next available Employee ID
app.get('/api/employees/next-id', async (req, res) => {
  try {
    const type = req.query.type || 'labour';
    const sheets = await getSheets();

    const sheetName = type === 'staff' ? STAFF_SALARY_SHEET : LABOUR_SALARY_SHEET;
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${sheetName}'!A:A`,
    });

    const rows = response.data.values || [];
    const ids = rows.slice(1).map(r => parseInt(r[0])).filter(n => !isNaN(n));
    const nextId = ids.length > 0 ? Math.max(...ids) + 1 : 1;

    res.json({ success: true, data: { nextId } });
  } catch (error) {
    console.error('Error getting next ID:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Create new employee
app.post('/api/employees', async (req, res) => {
  try {
    const sheets = await getSheets();
    const data = req.body;
    const isStaff = data.type === 'Staff';
    const sheetName = isStaff ? STAFF_SALARY_SHEET : LABOUR_SALARY_SHEET;

    // Get next ID if not provided
    let employeeId = data.employeeId;
    if (!employeeId) {
      const response = await sheets.spreadsheets.values.get({
        spreadsheetId: SALARY_SHEET_ID,
        range: `'${sheetName}'!A:A`,
      });
      const rows = response.data.values || [];
      const ids = rows.slice(1).map(r => parseInt(r[0])).filter(n => !isNaN(n));
      employeeId = ids.length > 0 ? Math.max(...ids) + 1 : 1;
    }

    // Prepare row data based on sheet type
    let rowData;
    if (isStaff) {
      // Staff: Employee ID, Name, Rate per day, Designation, Deductions, Net salary, Type
      rowData = [[
        String(employeeId),
        data.name || '',
        data.ratePerDay || '',
        data.designation || '',
        data.deductions || '',
        data.netSalary || '',
        'Staff'
      ]];
    } else {
      // Labour: Employee ID, Name, Designation, Rate per day, Rate per hour, OT hours, Net salary, Type
      rowData = [[
        String(employeeId),
        data.name || '',
        data.designation || '',
        data.ratePerDay || '',
        data.ratePerHour || (data.ratePerDay ? (parseFloat(data.ratePerDay) / 8).toFixed(2) : ''),
        data.otHours || '',
        data.netSalary || '',
        'Labour'
      ]];
    }

    await sheets.spreadsheets.values.append({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${sheetName}'!A:H`,
      valueInputOption: 'USER_ENTERED',
      insertDataOption: 'INSERT_ROWS',
      requestBody: { values: rowData }
    });

    res.status(201).json({
      success: true,
      data: {
        id: String(employeeId),
        employeeId: String(employeeId),
        name: data.name,
        designation: data.designation,
        ratePerDay: parseFloat(data.ratePerDay) || 0,
        ratePerHour: parseFloat(data.ratePerHour) || 0,
        otHours: parseFloat(data.otHours) || 0,
        netSalary: parseFloat(data.netSalary) || 0,
        type: isStaff ? 'Staff' : 'Labour',
        sheetType: isStaff ? 'staff' : 'labour'
      }
    });
  } catch (error) {
    console.error('Error creating employee:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Update employee
app.put('/api/employees/:id', async (req, res) => {
  try {
    const employeeId = req.params.id;
    const sheets = await getSheets();
    const data = req.body;

    // Find employee in Labour sheet
    const labourResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:H`,
    });
    const labourRows = labourResponse.data.values || [];

    for (let i = 1; i < labourRows.length; i++) {
      if (labourRows[i][0] === employeeId) {
        const rowData = [[
          employeeId,
          data.name || labourRows[i][1] || '',
          data.designation || labourRows[i][2] || '',
          data.ratePerDay !== undefined ? data.ratePerDay : labourRows[i][3] || '',
          data.ratePerHour !== undefined ? data.ratePerHour : labourRows[i][4] || '',
          data.otHours !== undefined ? data.otHours : labourRows[i][5] || '',
          data.netSalary !== undefined ? data.netSalary : labourRows[i][6] || '',
          labourRows[i][7] || 'Labour'
        ]];

        await sheets.spreadsheets.values.update({
          spreadsheetId: SALARY_SHEET_ID,
          range: `'${LABOUR_SALARY_SHEET}'!A${i + 1}:H${i + 1}`,
          valueInputOption: 'USER_ENTERED',
          requestBody: { values: rowData }
        });

        return res.json({ success: true, data: { ...data, id: employeeId, type: 'Labour' } });
      }
    }

    // Find in Staff sheet
    const staffResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${STAFF_SALARY_SHEET}'!A:G`,
    });
    const staffRows = staffResponse.data.values || [];

    for (let i = 1; i < staffRows.length; i++) {
      if (staffRows[i][0] === employeeId) {
        const rowData = [[
          employeeId,
          data.name || staffRows[i][1] || '',
          data.ratePerDay !== undefined ? data.ratePerDay : staffRows[i][2] || '',
          data.designation || staffRows[i][3] || '',
          data.deductions !== undefined ? data.deductions : staffRows[i][4] || '',
          data.netSalary !== undefined ? data.netSalary : staffRows[i][5] || '',
          staffRows[i][6] || 'Staff'
        ]];

        await sheets.spreadsheets.values.update({
          spreadsheetId: SALARY_SHEET_ID,
          range: `'${STAFF_SALARY_SHEET}'!A${i + 1}:G${i + 1}`,
          valueInputOption: 'USER_ENTERED',
          requestBody: { values: rowData }
        });

        return res.json({ success: true, data: { ...data, id: employeeId, type: 'Staff' } });
      }
    }

    res.status(404).json({ success: false, error: 'Employee not found' });
  } catch (error) {
    console.error('Error updating employee:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Delete employee
app.delete('/api/employees/:id', async (req, res) => {
  try {
    const employeeId = req.params.id;
    const sheets = await getSheets();

    // Search in Labour sheet
    const labourResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:A`,
    });
    const labourRows = labourResponse.data.values || [];

    for (let i = 1; i < labourRows.length; i++) {
      if (labourRows[i][0] === employeeId) {
        // Clear the row
        await sheets.spreadsheets.values.update({
          spreadsheetId: SALARY_SHEET_ID,
          range: `'${LABOUR_SALARY_SHEET}'!A${i + 1}:H${i + 1}`,
          valueInputOption: 'RAW',
          requestBody: { values: [['', '', '', '', '', '', '', '']] }
        });
        return res.json({ success: true, message: `Employee ${employeeId} deleted from Labour` });
      }
    }

    // Search in Staff sheet
    const staffResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${STAFF_SALARY_SHEET}'!A:A`,
    });
    const staffRows = staffResponse.data.values || [];

    for (let i = 1; i < staffRows.length; i++) {
      if (staffRows[i][0] === employeeId) {
        await sheets.spreadsheets.values.update({
          spreadsheetId: SALARY_SHEET_ID,
          range: `'${STAFF_SALARY_SHEET}'!A${i + 1}:G${i + 1}`,
          valueInputOption: 'RAW',
          requestBody: { values: [['', '', '', '', '', '', '']] }
        });
        return res.json({ success: true, message: `Employee ${employeeId} deleted from Staff` });
      }
    }

    res.status(404).json({ success: false, error: 'Employee not found' });
  } catch (error) {
    console.error('Error deleting employee:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Move employee between Labour and Staff
app.post('/api/employees/:id/move', async (req, res) => {
  try {
    const employeeId = req.params.id;
    const { targetType } = req.body; // 'labour' or 'staff'

    if (!targetType || !['labour', 'staff'].includes(targetType)) {
      return res.status(400).json({ success: false, error: 'targetType must be "labour" or "staff"' });
    }

    const sheets = await getSheets();
    const sourceSheet = targetType === 'labour' ? STAFF_SALARY_SHEET : LABOUR_SALARY_SHEET;
    const targetSheet = targetType === 'labour' ? LABOUR_SALARY_SHEET : STAFF_SALARY_SHEET;
    const sourceAttSheet = targetType === 'labour' ? STAFF_ATTENDANCE_SHEET : LABOUR_ATTENDANCE_SHEET;
    const targetAttSheet = targetType === 'labour' ? LABOUR_ATTENDANCE_SHEET : STAFF_ATTENDANCE_SHEET;

    // 1. Get employee data from source salary sheet
    const sourceRes = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${sourceSheet}'!A:H`,
    });
    const sourceRows = sourceRes.data.values || [];

    let employeeData = null;
    let sourceRowIndex = -1;

    for (let i = 1; i < sourceRows.length; i++) {
      if (sourceRows[i][0] === employeeId) {
        employeeData = sourceRows[i];
        sourceRowIndex = i;
        break;
      }
    }

    if (!employeeData) {
      return res.status(404).json({ success: false, error: `Employee ${employeeId} not found in ${sourceSheet}` });
    }

    // 2. Prepare data for target sheet
    // ACTUAL column structures (different order!):
    // Labour: [ID, Name, Designation, RatePerDay, RatePerHour, OTHours, NetSalary, Type]
    // Staff:  [ID, Name, RatePerDay, Designation, Deductions, NetSalary, Type]
    let targetData;
    if (targetType === 'labour') {
      // Moving Staff -> Labour
      // Staff columns: [0]=ID, [1]=Name, [2]=RatePerDay, [3]=Designation, [4]=Deductions, [5]=NetSalary, [6]=Type
      const ratePerDay = parseFloat(employeeData[2]) || 0;
      const ratePerHour = (ratePerDay / 8).toFixed(2);
      targetData = [
        employeeData[0],        // ID
        employeeData[1],        // Name
        employeeData[3] || '',  // Designation (was index 3 in Staff)
        employeeData[2] || '',  // RatePerDay (was index 2 in Staff)
        ratePerHour,            // RatePerHour (calculated)
        '0',                    // OTHours (default)
        '',                     // NetSalary (recalculated later)
        'Labour'                // Type
      ];
    } else {
      // Moving Labour -> Staff
      // Labour columns: [0]=ID, [1]=Name, [2]=Designation, [3]=RatePerDay, [4]=RatePerHour, [5]=OTHours, [6]=NetSalary, [7]=Type
      targetData = [
        employeeData[0],        // ID
        employeeData[1],        // Name
        employeeData[3] || '',  // RatePerDay (was index 3 in Labour)
        employeeData[2] || '',  // Designation (was index 2 in Labour)
        '0',                    // Deductions (default)
        '',                     // NetSalary (recalculated later)
        'Staff'                 // Type
      ];
    }

    // 3. Find next empty row in target sheet and append
    const targetRes = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${targetSheet}'!A:A`,
    });
    const targetRows = targetRes.data.values || [];
    const nextRow = targetRows.length + 1;

    await sheets.spreadsheets.values.update({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${targetSheet}'!A${nextRow}`,
      valueInputOption: 'RAW',
      requestBody: { values: [targetData] }
    });

    // 4. Clear from source sheet
    const clearCols = targetType === 'labour' ? 'A:G' : 'A:H';
    const clearValues = targetType === 'labour'
      ? [['', '', '', '', '', '', '']]
      : [['', '', '', '', '', '', '', '']];

    await sheets.spreadsheets.values.update({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${sourceSheet}'!A${sourceRowIndex + 1}:${clearCols.split(':')[1]}${sourceRowIndex + 1}`,
      valueInputOption: 'RAW',
      requestBody: { values: clearValues }
    });

    // 5. Move attendance data
    const sourceAttRes = await sheets.spreadsheets.values.get({
      spreadsheetId: ATTENDANCE_SHEET_ID,
      range: `'${sourceAttSheet}'!A:AH`,
    });
    const sourceAttRows = sourceAttRes.data.values || [];

    for (let i = 1; i < sourceAttRows.length; i++) {
      if (sourceAttRows[i][0] === employeeId) {
        const attData = sourceAttRows[i];

        // Append to target attendance sheet
        const targetAttRes = await sheets.spreadsheets.values.get({
          spreadsheetId: ATTENDANCE_SHEET_ID,
          range: `'${targetAttSheet}'!A:A`,
        });
        const targetAttRows = targetAttRes.data.values || [];
        const nextAttRow = targetAttRows.length + 1;

        await sheets.spreadsheets.values.update({
          spreadsheetId: ATTENDANCE_SHEET_ID,
          range: `'${targetAttSheet}'!A${nextAttRow}`,
          valueInputOption: 'RAW',
          requestBody: { values: [attData] }
        });

        // Clear from source attendance
        const clearAttValues = new Array(attData.length).fill('');
        await sheets.spreadsheets.values.update({
          spreadsheetId: ATTENDANCE_SHEET_ID,
          range: `'${sourceAttSheet}'!A${i + 1}:AH${i + 1}`,
          valueInputOption: 'RAW',
          requestBody: { values: [clearAttValues] }
        });

        break;
      }
    }

    res.json({
      success: true,
      message: `Employee ${employeeId} moved from ${sourceSheet} to ${targetSheet}`
    });
  } catch (error) {
    console.error('Error moving employee:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Fix missing salary record - create from attendance data
app.post('/api/employees/:id/fix-missing', async (req, res) => {
  try {
    const employeeId = req.params.id;
    const { targetType, ratePerDay, ratePerHour, otHours } = req.body;

    if (!targetType || !['labour', 'staff'].includes(targetType)) {
      return res.status(400).json({ success: false, error: 'targetType must be "labour" or "staff"' });
    }

    const sheets = await getSheets();

    // 1. Get employee data from attendance
    const attSheet = targetType === 'labour' ? LABOUR_ATTENDANCE_SHEET : STAFF_ATTENDANCE_SHEET;
    const attRes = await sheets.spreadsheets.values.get({
      spreadsheetId: ATTENDANCE_SHEET_ID,
      range: `'${attSheet}'!A:C`,
    });
    const attRows = attRes.data.values || [];

    let employeeData = null;
    for (let i = 1; i < attRows.length; i++) {
      if (attRows[i][0] === employeeId) {
        employeeData = {
          id: attRows[i][0],
          name: attRows[i][1],
          designation: attRows[i][2] || ''
        };
        break;
      }
    }

    if (!employeeData) {
      return res.status(404).json({ success: false, error: `Employee ${employeeId} not found in ${attSheet}` });
    }

    // 2. Check if already exists in salary
    const salSheet = targetType === 'labour' ? LABOUR_SALARY_SHEET : STAFF_SALARY_SHEET;
    const salRes = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${salSheet}'!A:A`,
    });
    const salRows = salRes.data.values || [];

    for (let i = 1; i < salRows.length; i++) {
      if (salRows[i][0] === employeeId) {
        return res.status(400).json({ success: false, error: `Employee ${employeeId} already exists in ${salSheet}` });
      }
    }

    // 3. Add to salary sheet
    const nextRow = salRows.length + 1;
    let rowData;

    if (targetType === 'labour') {
      // Labour: ID, Name, Designation, Rate/Day, Rate/Hour, OT Hours, Deduction, Net Salary
      rowData = [
        employeeData.id,
        employeeData.name,
        employeeData.designation,
        ratePerDay || 0,
        ratePerHour || 0,
        otHours || 0,
        0, // Deduction
        0  // Net Salary (calculated elsewhere)
      ];
    } else {
      // Staff: ID, Name, Designation, Rate/Day, Rate/Hour, Deduction, Net Salary
      rowData = [
        employeeData.id,
        employeeData.name,
        employeeData.designation,
        ratePerDay || 0,
        ratePerHour || 0,
        0, // Deduction
        0  // Net Salary
      ];
    }

    await sheets.spreadsheets.values.update({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${salSheet}'!A${nextRow}`,
      valueInputOption: 'RAW',
      requestBody: { values: [rowData] }
    });

    res.json({
      success: true,
      message: `Created salary record for ${employeeData.name} (ID: ${employeeId}) in ${salSheet}`,
      data: {
        id: employeeData.id,
        name: employeeData.name,
        designation: employeeData.designation,
        type: targetType
      }
    });
  } catch (error) {
    console.error('Error fixing missing employee:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// ============================================
// ATTENDANCE API ENDPOINTS
// ============================================

// Get attendance - supports ?type=labour|staff|all (default: all), ?month=0-11, ?year=2026
app.get('/api/attendance', async (req, res) => {
  try {
    const sheets = await getSheets();
    const type = req.query.type || 'all';
    const month = req.query.month !== undefined ? parseInt(req.query.month) : new Date().getMonth();
    const year = req.query.year !== undefined ? parseInt(req.query.year) : new Date().getFullYear();
    const attendance = [];

    const parseAttendanceRows = (rows, sheetType) => {
      for (let i = 1; i < rows.length; i++) {
        const row = rows[i];
        if (!row[0]) continue;
        const record = {
          employeeId: row[0],
          name: row[1],
          designation: row[2],
          rowIndex: i + 1,
          type: sheetType,
          days: {}
        };
        for (let day = 1; day <= 31; day++) {
          const colIndex = day + 2;
          if (colIndex < row.length) {
            record.days[day] = row[colIndex] || '';
          }
        }
        attendance.push(record);
      }
    };

    if (type === 'labour' || type === 'all') {
      const labourSheetName = await getOrCreateMonthlySheet(sheets, LABOUR_ATTENDANCE_SHEET, month, year);
      const labourRes = await sheets.spreadsheets.values.get({
        spreadsheetId: ATTENDANCE_SHEET_ID,
        range: `'${labourSheetName}'!A:AH`,
      });
      parseAttendanceRows(labourRes.data.values || [], 'labour');
    }

    if (type === 'staff' || type === 'all') {
      try {
        const staffSheetName = await getOrCreateMonthlySheet(sheets, STAFF_ATTENDANCE_SHEET, month, year);
        const staffRes = await sheets.spreadsheets.values.get({
          spreadsheetId: ATTENDANCE_SHEET_ID,
          range: `'${staffSheetName}'!A:AH`,
        });
        parseAttendanceRows(staffRes.data.values || [], 'staff');
      } catch (staffErr) {
        console.warn('Staff attendance sheet not found or error:', staffErr.message);
      }
    }

    res.json({ success: true, data: attendance });
  } catch (error) {
    console.error('Error fetching attendance:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Update attendance for a specific employee and day
app.put('/api/attendance/:employeeId/:day', async (req, res) => {
  try {
    const { employeeId, day } = req.params;
    const { status, type, month, year } = req.body;
    const dayNum = parseInt(day);
    const monthNum = month !== undefined ? parseInt(month) : new Date().getMonth();
    const yearNum = year !== undefined ? parseInt(year) : new Date().getFullYear();

    if (dayNum < 1 || dayNum > 31) {
      return res.status(400).json({ success: false, error: 'Invalid day' });
    }

    const sheets = await getSheets();
    const baseSheetName = type === 'staff' ? STAFF_ATTENDANCE_SHEET : LABOUR_ATTENDANCE_SHEET;
    const sheetName = await getOrCreateMonthlySheet(sheets, baseSheetName, monthNum, yearNum);

    // Find the employee row
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: ATTENDANCE_SHEET_ID,
      range: `'${sheetName}'!A:A`,
    });
    const rows = response.data.values || [];

    let targetRow = -1;
    for (let i = 1; i < rows.length; i++) {
      if (rows[i][0] === employeeId) {
        targetRow = i + 1;
        break;
      }
    }

    if (targetRow === -1) {
      return res.status(404).json({ success: false, error: 'Employee not found in attendance sheet' });
    }

    // Column for the day (Day 1 = Column D = 4th column, Day 31 = Column AH = 34th column)
    const colLetter = getColumnLetter(dayNum + 3);

    await sheets.spreadsheets.values.update({
      spreadsheetId: ATTENDANCE_SHEET_ID,
      range: `'${sheetName}'!${colLetter}${targetRow}`,
      valueInputOption: 'RAW',
      requestBody: { values: [[normalizeStatus(status)]] }
    });

    res.json({ success: true, message: `Updated ${employeeId} day ${day} to ${normalizeStatus(status)}` });
  } catch (error) {
    console.error('Error updating attendance:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Bulk update attendance for multiple employees on a day
app.post('/api/attendance/bulk', async (req, res) => {
  try {
    const { day, updates, type, month, year } = req.body; // updates: [{ employeeId, status, type? }, ...]
    const dayNum = parseInt(day);
    const monthNum = month !== undefined ? parseInt(month) : new Date().getMonth();
    const yearNum = year !== undefined ? parseInt(year) : new Date().getFullYear();

    if (dayNum < 1 || dayNum > 31) {
      return res.status(400).json({ success: false, error: 'Invalid day' });
    }

    const sheets = await getSheets();
    const colLetter = getColumnLetter(dayNum + 3);

    // Group updates by sheet type
    const grouped = {};
    if (type && type !== 'all') {
      grouped[type] = updates;
    } else {
      for (const update of updates) {
        const t = update.type || 'labour';
        if (!grouped[t]) grouped[t] = [];
        grouped[t].push(update);
      }
    }

    let totalUpdated = 0;

    for (const [sheetType, sheetUpdates] of Object.entries(grouped)) {
      const baseSheetName = sheetType === 'staff' ? STAFF_ATTENDANCE_SHEET : LABOUR_ATTENDANCE_SHEET;
      const sheetName = await getOrCreateMonthlySheet(sheets, baseSheetName, monthNum, yearNum);

      const response = await sheets.spreadsheets.values.get({
        spreadsheetId: ATTENDANCE_SHEET_ID,
        range: `'${sheetName}'!A:A`,
      });
      const rows = response.data.values || [];

      const employeeRowMap = {};
      for (let i = 1; i < rows.length; i++) {
        if (rows[i][0]) {
          employeeRowMap[rows[i][0]] = i + 1;
        }
      }

      const batchData = [];
      for (const update of sheetUpdates) {
        const rowNum = employeeRowMap[update.employeeId];
        if (rowNum) {
          batchData.push({
            range: `'${sheetName}'!${colLetter}${rowNum}`,
            values: [[normalizeStatus(update.status)]]
          });
        }
      }

      if (batchData.length > 0) {
        await sheets.spreadsheets.values.batchUpdate({
          spreadsheetId: ATTENDANCE_SHEET_ID,
          requestBody: {
            data: batchData,
            valueInputOption: 'RAW'
          }
        });
        totalUpdated += batchData.length;
      }
    }

    res.json({ success: true, message: `Updated ${totalUpdated} attendance records` });
  } catch (error) {
    console.error('Error bulk updating attendance:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// ============================================
// PAYROLL API ENDPOINTS
// ============================================

const PAYROLL_SHEET_ID = '1_q5QsmF9gZ2jeJqDpcSHU2iCeJyjIAQntK92G4iFsOg';
const MONTH_ABBRS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const LABOUR_PAYROLL_HEADERS = ['Employee ID', 'Name of Employee', 'Designation', 'Deductions', 'Paid Days', 'RATE PER hour', 'total bf OT', 'OT Hours', 'OT Pay', 'Net salary', 'Payment Method'];
const STAFF_PAYROLL_HEADERS  = ['Employee ID', 'Name of Employee', 'Designation', 'Deductions', 'Paid Days', 'RATE PER hour', 'Net salary', 'Payment Method'];

function payrollRowToArray(row, type) {
  const paymentMethod = row.paymentMethod || (row.isCash ? 'Cash' : 'Bank Transfer');
  if (type === 'staff') {
    return [row.employeeId, row.name, row.designation, row.deductionAmount, row.paidDays, row.ratePerHour, row.netSalary, paymentMethod];
  }
  return [row.employeeId, row.name, row.designation, row.deductionAmount, row.paidDays, row.ratePerHour, row.salaryBeforeOT, row.otHours, row.otPay, row.netSalary, paymentMethod];
}

// Write calculated payroll to Monthly Payroll sheet
app.post('/api/payroll', async (req, res) => {
  try {
    const { month, year, type, data } = req.body;
    if (month === undefined || !year || !type || !Array.isArray(data)) {
      return res.status(400).json({ success: false, error: 'Missing required fields: month, year, type, data' });
    }

    // Debug: Log received cash employees
    const receivedCashCount = data.filter(row => row.isCash === true || row.paymentMethod === 'Cash').length;
    console.log(`[Payroll POST] Received ${data.length} records, ${receivedCashCount} marked as cash`);

    const sheets = await getSheets();
    const tabName = `${type === 'staff' ? 'Staff' : 'Labour'} ${MONTH_ABBRS[month]}`;

    // 1. Get current sheet metadata to check if tab exists
    const spreadsheet = await sheets.spreadsheets.get({ spreadsheetId: PAYROLL_SHEET_ID });
    const existingSheets = spreadsheet.data.sheets.map(s => s.properties.title);

    // 2. Create tab if it doesn't exist
    if (!existingSheets.includes(tabName)) {
      await sheets.spreadsheets.batchUpdate({
        spreadsheetId: PAYROLL_SHEET_ID,
        requestBody: {
          requests: [{
            addSheet: { properties: { title: tabName } }
          }]
        }
      });
    }

    // 3. Clear the tab
    await sheets.spreadsheets.values.clear({
      spreadsheetId: PAYROLL_SHEET_ID,
      range: `'${tabName}'!A:Z`,
    });

    // 4. Build rows: header + data
    const headers = type === 'staff' ? STAFF_PAYROLL_HEADERS : LABOUR_PAYROLL_HEADERS;
    const rows = [headers, ...data.map(row => payrollRowToArray(row, type))];

    // 5. Write all at once
    await sheets.spreadsheets.values.update({
      spreadsheetId: PAYROLL_SHEET_ID,
      range: `'${tabName}'!A1`,
      valueInputOption: 'RAW',
      requestBody: { values: rows }
    });

    // 6. Also save Cash employees to separate "Cash Jan" sheet
    const cashData = data.filter(row => row.isCash || row.paymentMethod === 'Cash');
    if (cashData.length > 0) {
      const cashTabName = `Cash ${MONTH_ABBRS[month]}`;

      // Create cash tab if it doesn't exist
      const updatedSpreadsheet = await sheets.spreadsheets.get({ spreadsheetId: PAYROLL_SHEET_ID });
      const updatedSheets = updatedSpreadsheet.data.sheets.map(s => s.properties.title);

      if (!updatedSheets.includes(cashTabName)) {
        await sheets.spreadsheets.batchUpdate({
          spreadsheetId: PAYROLL_SHEET_ID,
          requestBody: {
            requests: [{
              addSheet: { properties: { title: cashTabName } }
            }]
          }
        });
      }

      // Clear and write cash data
      await sheets.spreadsheets.values.clear({
        spreadsheetId: PAYROLL_SHEET_ID,
        range: `'${cashTabName}'!A:Z`,
      });

      const cashRows = [headers, ...cashData.map(row => payrollRowToArray(row, type))];
      await sheets.spreadsheets.values.update({
        spreadsheetId: PAYROLL_SHEET_ID,
        range: `'${cashTabName}'!A1`,
        valueInputOption: 'RAW',
        requestBody: { values: cashRows }
      });
    }

    res.json({ success: true, message: `Wrote ${data.length} records to "${tabName}"${cashData.length > 0 ? ` and ${cashData.length} cash records to "Cash ${MONTH_ABBRS[month]}"` : ''}` });
  } catch (error) {
    console.error('Error writing payroll:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// GET saved payroll data for a specific month/year/type
app.get('/api/payroll', async (req, res) => {
  try {
    const { month, year, type } = req.query;
    if (month === undefined || !year || !type) {
      return res.status(400).json({ success: false, error: 'Missing required query params: month, year, type' });
    }

    const sheets = await getSheets();
    const tabName = `${type === 'staff' ? 'Staff' : 'Labour'} ${MONTH_ABBRS[parseInt(month)]}`;

    // Check if tab exists
    const spreadsheet = await sheets.spreadsheets.get({ spreadsheetId: PAYROLL_SHEET_ID });
    const existingSheets = spreadsheet.data.sheets.map(s => s.properties.title);

    if (!existingSheets.includes(tabName)) {
      return res.json({ success: true, data: [], message: 'No saved data for this period' });
    }

    // Read the data
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: PAYROLL_SHEET_ID,
      range: `'${tabName}'!A:Z`,
    });

    const rows = response.data.values || [];
    if (rows.length <= 1) {
      return res.json({ success: true, data: [], message: 'No saved data for this period' });
    }

    // Parse rows into objects (skip header row)
    const headers = rows[0];
    const isStaff = type === 'staff';

    // Helper to parse numbers that may have commas
    const parseNum = (val) => parseFloat(String(val || '0').replace(/,/g, '')) || 0;

    const data = rows.slice(1).map(row => {
      if (isStaff) {
        // Staff headers: Employee ID, Name, Designation, Deductions, Paid Days, Rate/Hr, Net salary, Payment Method
        return {
          employeeId: row[0],
          name: row[1],
          designation: row[2],
          deductionAmount: parseNum(row[3]),
          paidDays: parseNum(row[4]),
          ratePerHour: parseNum(row[5]),
          netSalary: parseNum(row[6]),
          paymentMethod: row[7] || 'Bank Transfer',
          isCash: row[7] === 'Cash',
        };
      } else {
        // Labour headers: Employee ID, Name, Designation, Deductions, Paid Days, Rate/Hr, total bf OT, OT Hours, OT Pay, Net salary, Payment Method
        return {
          employeeId: row[0],
          name: row[1],
          designation: row[2],
          deductionAmount: parseNum(row[3]),
          paidDays: parseNum(row[4]),
          ratePerHour: parseNum(row[5]),
          salaryBeforeOT: parseNum(row[6]),
          otHours: parseNum(row[7]),
          otPay: parseNum(row[8]),
          netSalary: parseNum(row[9]),
          paymentMethod: row[10] || 'Bank Transfer',
          isCash: row[10] === 'Cash',
        };
      }
    });

    res.json({ success: true, data, savedFromSheet: true });
  } catch (error) {
    console.error('Error loading payroll:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// ============================================
// OT/DEDUCTIONS API ENDPOINTS
// ============================================

// Update OT hours for an employee (in Salary sheet)
app.put('/api/employees/:id/ot', async (req, res) => {
  try {
    const employeeId = req.params.id;
    const { otHours } = req.body;
    const sheets = await getSheets();

    // Find in Labour sheet (OT hours is column F, index 5)
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:A`,
    });
    const rows = response.data.values || [];

    for (let i = 1; i < rows.length; i++) {
      if (rows[i][0] === employeeId) {
        await sheets.spreadsheets.values.update({
          spreadsheetId: SALARY_SHEET_ID,
          range: `'${LABOUR_SALARY_SHEET}'!F${i + 1}`,
          valueInputOption: 'USER_ENTERED',
          requestBody: { values: [[otHours]] }
        });
        return res.json({ success: true, message: `Updated OT hours for ${employeeId}` });
      }
    }

    res.status(404).json({ success: false, error: 'Employee not found' });
  } catch (error) {
    console.error('Error updating OT:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Bulk update OT hours for multiple employees
app.post('/api/employees/ot/bulk', async (req, res) => {
  try {
    const { updates } = req.body; // [{ employeeId, otHours }, ...]
    const sheets = await getSheets();

    // Get all employee IDs and their row numbers
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: SALARY_SHEET_ID,
      range: `'${LABOUR_SALARY_SHEET}'!A:A`,
    });
    const rows = response.data.values || [];

    const employeeRowMap = {};
    for (let i = 1; i < rows.length; i++) {
      if (rows[i][0]) {
        employeeRowMap[rows[i][0]] = i + 1;
      }
    }

    // Prepare batch update
    const batchData = [];
    for (const update of updates) {
      const rowNum = employeeRowMap[update.employeeId];
      if (rowNum) {
        batchData.push({
          range: `'${LABOUR_SALARY_SHEET}'!F${rowNum}`,
          values: [[update.otHours]]
        });
      }
    }

    if (batchData.length > 0) {
      await sheets.spreadsheets.values.batchUpdate({
        spreadsheetId: SALARY_SHEET_ID,
        requestBody: {
          data: batchData,
          valueInputOption: 'USER_ENTERED'
        }
      });
    }

    res.json({ success: true, message: `Updated ${batchData.length} OT records` });
  } catch (error) {
    console.error('Error bulk updating OT:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// ============================================
// AI CHAT ENDPOINT
// ============================================

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

app.post('/api/ai/chat', async (req, res) => {
  try {
    const { message, history } = req.body;

    if (!message) {
      return res.status(400).json({ success: false, error: 'Message is required' });
    }

    if (!process.env.ANTHROPIC_API_KEY) {
      return res.status(500).json({ success: false, error: 'AI service not configured' });
    }

    const sheets = await getSheets();

    // Get current month's attendance sheets
    const currentMonth = new Date().getMonth();
    const currentYear = new Date().getFullYear();
    const labourAttSheetName = await getOrCreateMonthlySheet(sheets, LABOUR_ATTENDANCE_SHEET, currentMonth, currentYear);
    const staffAttSheetName = await getOrCreateMonthlySheet(sheets, STAFF_ATTENDANCE_SHEET, currentMonth, currentYear);

    // Fetch current data for context (including BPV, PDC, CDC)
    const [labourEmpRes, staffEmpRes, labourAttRes, staffAttRes, bpvRes, pdcStatusRes] = await Promise.all([
      sheets.spreadsheets.values.get({
        spreadsheetId: SALARY_SHEET_ID,
        range: `'${LABOUR_SALARY_SHEET}'!A:H`,
      }),
      sheets.spreadsheets.values.get({
        spreadsheetId: SALARY_SHEET_ID,
        range: `'${STAFF_SALARY_SHEET}'!A:G`,
      }),
      sheets.spreadsheets.values.get({
        spreadsheetId: ATTENDANCE_SHEET_ID,
        range: `'${labourAttSheetName}'!A:AH`,
      }),
      sheets.spreadsheets.values.get({
        spreadsheetId: ATTENDANCE_SHEET_ID,
        range: `'${staffAttSheetName}'!A:AH`,
      }),
      // Fetch all BPV vouchers
      sheets.spreadsheets.values.get({
        spreadsheetId: SHEET_ID,
        range: `'${ALL_BPV_SHEET}'!A:H`,
      }),
      // Fetch PDC tracker statuses
      sheets.spreadsheets.values.get({
        spreadsheetId: PDC_SHEET_ID,
        range: `'${PDC_SHEET_NAME}'!A:H`,
      }),
    ]);

    // Parse employees
    const labourEmps = (labourEmpRes.data.values || []).slice(1).filter(r => r[0] && r[1]);
    const staffEmps = (staffEmpRes.data.values || []).slice(1).filter(r => r[0] && r[1]);

    // Parse attendance - count statuses
    const parseAttendance = (rows) => {
      const summary = [];
      for (let i = 1; i < (rows || []).length; i++) {
        const row = rows[i];
        if (!row[0]) continue;
        let present = 0, absent = 0, leave = 0, off = 0, sick = 0;
        for (let d = 3; d <= 33; d++) {
          const status = (row[d] || '').toLowerCase();
          if (status === 'present') present++;
          else if (status === 'absent') absent++;
          else if (status === 'leave') leave++;
          else if (status === 'off') off++;
          else if (status === 'sick') sick++;
        }
        summary.push({ id: row[0], name: row[1], designation: row[2], present, absent, leave, off, sick });
      }
      return summary;
    };

    const labourAtt = parseAttendance(labourAttRes.data.values);
    const staffAtt = parseAttendance(staffAttRes.data.values);

    // Parse BPV vouchers (columns: BPV No, Company, Description, Date, Cheque No, Cheque Date, Amount, Type)
    const bpvRows = (bpvRes.data.values || []).slice(1).filter(r => r[0]);
    const bpvVouchers = bpvRows.map(r => ({
      bpvNo: r[0],
      company: r[1] || '',
      description: r[2] || '',
      date: r[3] || '',
      chequeNo: r[4] || '',
      chequeDate: r[5] || '',
      amount: parseFloat((r[6] || '0').replace(/,/g, '')) || 0,
      type: r[7] || 'Cash'
    }));

    // Parse PDC tracker statuses (columns: BPV No, Company, Description, Cheque No, Cheque Date, Amount, Status, Notes)
    const pdcRows = (pdcStatusRes.data.values || []).slice(1).filter(r => r[3]); // Must have cheque number
    const pdcStatusMap = {};
    pdcRows.forEach(r => {
      if (r[3]) pdcStatusMap[r[3]] = r[6] || 'Not Released';
    });

    // Extract PDC cheques (type = PDC)
    const pdcCheques = bpvVouchers.filter(v => v.type?.toUpperCase() === 'PDC' && v.chequeNo).map(v => ({
      ...v,
      status: pdcStatusMap[v.chequeNo] || 'Not Released'
    }));
    const pdcNotReleased = pdcCheques.filter(p => p.status === 'Not Released');
    const pdcReleased = pdcCheques.filter(p => p.status === 'Released');
    const pdcTotalAmount = pdcCheques.reduce((sum, p) => sum + p.amount, 0);

    // Extract CDC cheques (type = CDC)
    const cdcCheques = bpvVouchers.filter(v => v.type?.toUpperCase() === 'CDC' && v.chequeNo).map(v => ({
      ...v,
      status: pdcStatusMap[v.chequeNo] || 'Pending'
    }));
    const cdcPending = cdcCheques.filter(c => c.status === 'Pending');
    const cdcCleared = cdcCheques.filter(c => c.status === 'Cleared');
    const cdcTotalAmount = cdcCheques.reduce((sum, c) => sum + c.amount, 0);

    // Build context summary
    const today = new Date();
    const monthName = today.toLocaleString('default', { month: 'long', year: 'numeric' });

    const context = `
You are an HR & Finance Assistant for Newell Electromechanical Works LLC.
You have access to employee data, attendance, BPV vouchers, PDC cheques, and CDC cheques.

CURRENT DATA (${monthName}):

=== EMPLOYEES ===
LABOUR EMPLOYEES (${labourEmps.length} total):
${labourEmps.slice(0, 20).map(e => `- ID ${e[0]}: ${e[1]} (${e[2]}) - Rate/Day: ${e[3]} AED, OT Hours: ${e[5] || 0}`).join('\n')}
${labourEmps.length > 20 ? `...and ${labourEmps.length - 20} more` : ''}

STAFF EMPLOYEES (${staffEmps.length} total):
${staffEmps.slice(0, 10).map(e => `- ID ${e[0]}: ${e[1]} (${e[2]}) - Rate/Day: ${e[3]} AED`).join('\n')}
${staffEmps.length > 10 ? `...and ${staffEmps.length - 10} more` : ''}

=== ATTENDANCE ===
Labour attendance:
${labourAtt.slice(0, 15).map(a => `- ${a.name}: ${a.present} present, ${a.absent} absent, ${a.leave} leave`).join('\n')}
${labourAtt.length > 15 ? `...and ${labourAtt.length - 15} more` : ''}

Most absent Labour employees: ${labourAtt.sort((a,b) => b.absent - a.absent).slice(0,5).map(a => `${a.name} (${a.absent} days)`).join(', ')}

Staff attendance:
${staffAtt.slice(0, 10).map(a => `- ${a.name}: ${a.present} present, ${a.absent} absent, ${a.leave} leave`).join('\n')}

=== BPV VOUCHERS (${bpvVouchers.length} total) ===
${bpvVouchers.slice(0, 30).map(v => `- BPV #${v.bpvNo}: ${v.company} | ${v.amount.toLocaleString()} AED | Type: ${v.type} | Cheque: ${v.chequeNo || 'N/A'} | Date: ${v.chequeDate || v.date}`).join('\n')}
${bpvVouchers.length > 30 ? `...and ${bpvVouchers.length - 30} more vouchers` : ''}

=== PDC CHEQUES (Post-Dated) - ${pdcCheques.length} total, ${pdcNotReleased.length} Not Released, ${pdcReleased.length} Released ===
Total PDC Amount: ${pdcTotalAmount.toLocaleString()} AED
${pdcCheques.slice(0, 25).map(p => `- Cheque #${p.chequeNo}: ${p.company} | ${p.amount.toLocaleString()} AED | Date: ${p.chequeDate} | Status: ${p.status} | BPV #${p.bpvNo}`).join('\n')}
${pdcCheques.length > 25 ? `...and ${pdcCheques.length - 25} more PDC cheques` : ''}

=== CDC CHEQUES (Current-Dated) - ${cdcCheques.length} total, ${cdcPending.length} Pending, ${cdcCleared.length} Cleared ===
Total CDC Amount: ${cdcTotalAmount.toLocaleString()} AED
${cdcCheques.slice(0, 25).map(c => `- Cheque #${c.chequeNo}: ${c.company} | ${c.amount.toLocaleString()} AED | Date: ${c.chequeDate} | Status: ${c.status} | BPV #${c.bpvNo}`).join('\n')}
${cdcCheques.length > 25 ? `...and ${cdcCheques.length - 25} more CDC cheques` : ''}

=== TOTALS ===
- Total employees: ${labourEmps.length + staffEmps.length} (Labour: ${labourEmps.length}, Staff: ${staffEmps.length})
- Total BPV vouchers: ${bpvVouchers.length}
- Total PDC cheques: ${pdcCheques.length} (${pdcTotalAmount.toLocaleString()} AED)
- Total CDC cheques: ${cdcCheques.length} (${cdcTotalAmount.toLocaleString()} AED)

=== PAYROLL CALCULATION ===
- Net Salary = (Rate/Day * Paid Days) + (OT Hours * Rate/Hour * 1.25) for Labour
- Paid Days = Present + Off days
- OT Rate = Rate/Day / 8 * 1.25

CRITICAL: Reply like a normal person texting. NO markdown, NO asterisks, NO bullet points, NO dashes, NO formatting symbols.
Just plain simple text. Example: "Cheque 1035 is cleared. 10,000 AED to Salam Express, dated Jan 15."
Keep answers short - one or two sentences only.
`;

    // Build messages for Claude
    const messages = [];

    // Add history if provided
    if (history && Array.isArray(history)) {
      history.forEach(msg => {
        if (msg.role === 'user' || msg.role === 'assistant') {
          messages.push({ role: msg.role, content: msg.content });
        }
      });
    }

    // Add current message
    messages.push({ role: 'user', content: message });

    const response = await anthropic.messages.create({
      model: 'claude-sonnet-4-20250514',
      max_tokens: 1024,
      system: context,
      messages: messages,
    });

    const assistantResponse = response.content[0]?.text || 'Sorry, I could not generate a response.';

    res.json({ success: true, response: assistantResponse });
  } catch (error) {
    console.error('Error in AI chat:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// ============================================
// PDC (POST-DATED CHEQUES) API ENDPOINTS
// ============================================

// PDC Headers: [BPV No, Company, Description, Cheque No, Cheque Date, Amount, Status, Notes]
const PDC_HEADERS = ['BPV No', 'Company', 'Description', 'Cheque No', 'Cheque Date', 'Amount', 'Status', 'Notes'];

// Helper: Ensure PDC sheet and tracker tab exist
async function ensurePdcSheet(sheets) {
  try {
    const spreadsheet = await sheets.spreadsheets.get({ spreadsheetId: PDC_SHEET_ID });
    const existingSheets = spreadsheet.data.sheets.map(s => s.properties.title);

    if (!existingSheets.includes(PDC_SHEET_NAME)) {
      await sheets.spreadsheets.batchUpdate({
        spreadsheetId: PDC_SHEET_ID,
        requestBody: {
          requests: [{ addSheet: { properties: { title: PDC_SHEET_NAME } } }]
        }
      });
      // Add headers
      await sheets.spreadsheets.values.update({
        spreadsheetId: PDC_SHEET_ID,
        range: `'${PDC_SHEET_NAME}'!A1`,
        valueInputOption: 'RAW',
        requestBody: { values: [PDC_HEADERS] }
      });
    }
    return true;
  } catch (error) {
    console.error('Error ensuring PDC sheet:', error);
    return false;
  }
}

// Get all PDCs
app.get('/api/pdc', async (req, res) => {
  try {
    const sheets = await getSheets();
    await ensurePdcSheet(sheets);

    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!A:H`,
    });

    const rows = response.data.values || [];
    const pdcs = [];

    for (let i = 1; i < rows.length; i++) {
      const row = rows[i];
      if (row[0] || row[3]) { // Has BPV No or Cheque No
        pdcs.push({
          id: i,
          rowIndex: i + 1,
          bpvNo: row[0] || '',
          company: row[1] || '',
          description: row[2] || '',
          chequeNo: row[3] || '',
          chequeDate: row[4] || '',
          amount: parseFloat(String(row[5]).replace(/,/g, '')) || 0,
          status: row[6] || 'Pending',
          notes: row[7] || ''
        });
      }
    }

    res.json({ success: true, data: pdcs });
  } catch (error) {
    console.error('Error fetching PDCs:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Create new PDC
app.post('/api/pdc', async (req, res) => {
  try {
    const sheets = await getSheets();
    await ensurePdcSheet(sheets);

    const data = req.body;
    const rowData = [[
      data.bpvNo || '',
      data.company || '',
      data.description || '',
      data.chequeNo || '',
      data.chequeDate || '',
      data.amount || '',
      data.status || 'Pending',
      data.notes || ''
    ]];

    // Find next empty row (using column D - Cheque No, which always has data)
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!D:D`,
    });
    const nextRow = (response.data.values?.length || 1) + 1;

    await sheets.spreadsheets.values.update({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!A${nextRow}`,
      valueInputOption: 'USER_ENTERED',
      requestBody: { values: rowData }
    });

    res.json({ success: true, data: { ...data, id: nextRow - 1, rowIndex: nextRow } });
  } catch (error) {
    console.error('Error creating PDC:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Update PDC
app.put('/api/pdc/:id', async (req, res) => {
  try {
    const rowIndex = parseInt(req.params.id);
    const sheets = await getSheets();
    const data = req.body;

    const rowData = [[
      data.bpvNo || '',
      data.company || '',
      data.description || '',
      data.chequeNo || '',
      data.chequeDate || '',
      data.amount || '',
      data.status || 'Pending',
      data.notes || ''
    ]];

    await sheets.spreadsheets.values.update({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!A${rowIndex}:H${rowIndex}`,
      valueInputOption: 'USER_ENTERED',
      requestBody: { values: rowData }
    });

    res.json({ success: true, data: { ...data, rowIndex } });
  } catch (error) {
    console.error('Error updating PDC:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Update PDC status only
app.patch('/api/pdc/:id/status', async (req, res) => {
  try {
    const rowIndex = parseInt(req.params.id);
    const { status } = req.body;
    const sheets = await getSheets();

    await sheets.spreadsheets.values.update({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!G${rowIndex}`,
      valueInputOption: 'RAW',
      requestBody: { values: [[status]] }
    });

    res.json({ success: true, status });
  } catch (error) {
    console.error('Error updating PDC status:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Delete PDC
app.delete('/api/pdc/:id', async (req, res) => {
  try {
    const rowIndex = parseInt(req.params.id);
    const sheets = await getSheets();

    await sheets.spreadsheets.values.update({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!A${rowIndex}:H${rowIndex}`,
      valueInputOption: 'RAW',
      requestBody: { values: [['', '', '', '', '', '', '', '']] }
    });

    res.json({ success: true, message: 'PDC deleted' });
  } catch (error) {
    console.error('Error deleting PDC:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Get PDC statuses as a map (chequeNo -> status)
app.get('/api/pdc/statuses', async (req, res) => {
  try {
    const sheets = await getSheets();
    await ensurePdcSheet(sheets);

    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!A:H`,
    });

    const rows = response.data.values || [];
    const statusMap = {};

    for (let i = 1; i < rows.length; i++) {
      const row = rows[i];
      const chequeNo = row[3]; // Column D - Cheque No
      const status = row[6];   // Column G - Status
      if (chequeNo) {
        statusMap[chequeNo] = status || 'Pending';
      }
    }

    res.json({ success: true, data: statusMap });
  } catch (error) {
    console.error('Error fetching PDC statuses:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Update status by cheque number
app.patch('/api/pdc/status-by-cheque/:chequeNo', async (req, res) => {
  try {
    const chequeNo = decodeURIComponent(req.params.chequeNo);
    const { status } = req.body;
    const sheets = await getSheets();
    await ensurePdcSheet(sheets);

    // Find ALL rows with this cheque number (handle duplicates)
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!D:D`,
    });
    const rows = response.data.values || [];

    const matchingRows = [];
    for (let i = 1; i < rows.length; i++) {
      if (rows[i][0] === chequeNo) {
        matchingRows.push(i + 1); // Store row number (1-indexed)
      }
    }

    // If found, update ALL matching rows to handle duplicates
    if (matchingRows.length > 0) {
      for (const rowNum of matchingRows) {
        await sheets.spreadsheets.values.update({
          spreadsheetId: PDC_SHEET_ID,
          range: `'${PDC_SHEET_NAME}'!G${rowNum}`,
          valueInputOption: 'RAW',
          requestBody: { values: [[status]] }
        });
      }
      return res.json({ success: true, chequeNo, status, updatedRows: matchingRows.length });
    }

    // If cheque not found in PDC sheet, fetch full details from BPV and create entry
    const bpvResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${ALL_BPV_SHEET}'!A:H`,
    });
    const bpvRows = bpvResponse.data.values || [];

    // Find the line item with this cheque number
    let chequeData = null;
    for (let i = 1; i < bpvRows.length; i++) {
      const row = bpvRows[i];
      if (row[4] === chequeNo) { // Column E = Cheque No
        chequeData = {
          bpvNo: row[0] || '',
          company: row[1] || '',
          description: row[2] || '',
          chequeNo: row[4] || '',
          chequeDate: row[5] || '',
          amount: String(row[6] || '').replace(/,/g, '') // Remove commas for clean number
        };
        break;
      }
    }

    // Find next row in PDC tracker
    const allRowsResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!A:A`,
    });
    const nextRow = (allRowsResponse.data.values?.length || 1) + 1;

    // Create full entry with all details
    const rowData = chequeData
      ? [chequeData.bpvNo, chequeData.company, chequeData.description, chequeData.chequeNo, chequeData.chequeDate, chequeData.amount, status, '']
      : ['', '', '', chequeNo, '', '', status, ''];

    await sheets.spreadsheets.values.update({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!A${nextRow}`,
      valueInputOption: 'RAW',
      requestBody: { values: [rowData] }
    });

    res.json({ success: true, chequeNo, status, created: true });
  } catch (error) {
    console.error('Error updating PDC status:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Bulk update status for multiple cheques
app.patch('/api/pdc/bulk-status', async (req, res) => {
  try {
    const { chequeNumbers, status } = req.body;

    if (!chequeNumbers || !Array.isArray(chequeNumbers) || chequeNumbers.length === 0) {
      return res.status(400).json({ success: false, error: 'chequeNumbers array is required' });
    }
    if (!status) {
      return res.status(400).json({ success: false, error: 'status is required' });
    }

    const sheets = await getSheets();
    await ensurePdcSheet(sheets);

    // Get all cheque numbers from column D to find row indices
    const response = await sheets.spreadsheets.values.get({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!D:D`,
    });
    const rows = response.data.values || [];

    // Build batch update data
    const batchData = [];
    const updatedCheques = [];

    for (let i = 1; i < rows.length; i++) {
      const chequeNo = rows[i][0];
      if (chequeNumbers.includes(chequeNo)) {
        batchData.push({
          range: `'${PDC_SHEET_NAME}'!G${i + 1}`,
          values: [[status]]
        });
        updatedCheques.push(chequeNo);
      }
    }

    if (batchData.length > 0) {
      await sheets.spreadsheets.values.batchUpdate({
        spreadsheetId: PDC_SHEET_ID,
        requestBody: {
          data: batchData,
          valueInputOption: 'RAW'
        }
      });
    }

    res.json({
      success: true,
      status,
      updatedCount: updatedCheques.length,
      updatedCheques
    });
  } catch (error) {
    console.error('Error bulk updating PDC statuses:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Sync PDC from BPV (extract PDC entries from a voucher)
app.post('/api/pdc/sync-from-bpv/:bpvNo', async (req, res) => {
  try {
    const bpvNo = req.params.bpvNo;
    const sheets = await getSheets();
    await ensurePdcSheet(sheets);

    // Get BPV data from all Bpv sheet
    const bpvResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: SHEET_ID,
      range: `'${ALL_BPV_SHEET}'!A:H`,
    });
    const bpvRows = bpvResponse.data.values || [];

    // Find all line items for this BPV
    const lineItems = [];
    for (let i = 1; i < bpvRows.length; i++) {
      const row = bpvRows[i];
      if (row[0] === bpvNo) {
        lineItems.push({
          bpvNo: row[0],
          company: row[1] || '',
          description: row[2] || '',
          date: row[3] || '',
          chequeNo: row[4] || '',
          chequeDate: row[5] || '',
          amount: row[6] || '',
          type: row[7] || ''
        });
      }
    }

    if (lineItems.length === 0) {
      return res.status(404).json({ success: false, error: `No BPV found with number ${bpvNo}` });
    }

    // Check for PDC type entries and add to PDC tracker
    const pdcEntries = lineItems.filter(item =>
      item.type?.toUpperCase() === 'PDC' || item.chequeDate
    );

    if (pdcEntries.length === 0) {
      return res.json({ success: true, message: 'No PDC entries found in this BPV', added: 0 });
    }

    // Get existing PDCs to avoid duplicates
    const existingResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!D:D`,
    });
    const existingCheques = new Set((existingResponse.data.values || []).flat());

    // Find next row
    const pdcResponse = await sheets.spreadsheets.values.get({
      spreadsheetId: PDC_SHEET_ID,
      range: `'${PDC_SHEET_NAME}'!A:A`,
    });
    let nextRow = (pdcResponse.data.values?.length || 1) + 1;

    let added = 0;
    for (const entry of pdcEntries) {
      if (!existingCheques.has(entry.chequeNo)) {
        await sheets.spreadsheets.values.update({
          spreadsheetId: PDC_SHEET_ID,
          range: `'${PDC_SHEET_NAME}'!A${nextRow}`,
          valueInputOption: 'USER_ENTERED',
          requestBody: { values: [[
            entry.bpvNo,
            entry.company,
            entry.description,
            entry.chequeNo,
            entry.chequeDate,
            entry.amount,
            'Pending',
            ''
          ]] }
        });
        nextRow++;
        added++;
      }
    }

    res.json({ success: true, message: `Added ${added} PDC entries`, added });
  } catch (error) {
    console.error('Error syncing PDC from BPV:', error);
    res.status(500).json({ success: false, error: error.message });
  }
});

// Health check
app.get('/health', (req, res) => res.json({ status: 'ok' }));

const PORT = process.env.PORT || 3001;
app.listen(PORT, () => console.log(`BPV Backend running on port ${PORT}`));
